"""Tests for the loyalty point ledger + pricing (Stage 2)."""

import secrets
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.loyalty.codes import generate_batch
from src.loyalty.models import LedgerReason, PointLedger
from src.loyalty.pricing import (
    UnknownProductError,
    compute_cashback,
    get_unit_price,
)
from src.loyalty.service import earn_row_reconciles, get_balance
from src.products.models import Product
from src.users.models import User


async def _make_product(session: AsyncSession, *, price: str = "10000") -> int:
    """Seed a priced product; return its id (the cashback key)."""
    product = Product(
        name="Test BAD", slug=f"test-bad-{secrets.token_hex(4)}", price=Decimal(price)
    )
    session.add(product)
    await session.flush()
    return product.id


async def _make_qr_id(session: AsyncSession, product_id: int) -> int:
    """Create a real qr_code (+ batch) for a product; return its id — satisfies the FKs."""
    _, codes = await generate_batch(session, product_id, 1)
    return codes[0].id


def _cashback_row(user_id: int, product_id: int, qr_code_id: int | None) -> PointLedger:
    """A well-formed cashback earn row (5% of 10_000 = 500)."""
    return PointLedger(
        user_id=user_id,
        amount=compute_cashback(10_000, 500),
        reason=LedgerReason.CASHBACK,
        qr_code_id=qr_code_id,
        product_id=product_id,
        unit_price=10_000,
        rate_bps=500,
    )


# --------------------------------------------------------------------------- #
# pricing — money math + DB-lookup
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("unit_price", "rate_bps", "expected"),
    [
        (10_000, 500, 500),  # exact 5%
        (10_000, 800, 800),  # exact 8% (Stage 3 rate)
        (10_000, 250, 250),  # welcome half-rate
        (10_010, 500, 501),  # 500.5 → rounds UP (customer-favouring)
        (9_990, 500, 500),  # 499.5 → rounds UP
        (10, 500, 1),  # 0.5 → rounds UP to 1
        (1, 500, 0),  # 0.05 → 0
    ],
)
def test_compute_cashback_round_half_up(unit_price: int, rate_bps: int, expected: int) -> None:
    """Cashback is exact integer points; a .5 tie rounds up (in the customer's favour)."""
    assert compute_cashback(unit_price, rate_bps) == expected


async def test_get_unit_price_known_and_unknown(test_db: AsyncSession) -> None:
    """A priced product returns its price; a missing product_id raises (never returns 0)."""
    pid = await _make_product(test_db, price="10000")
    assert await get_unit_price(test_db, pid) == 10_000
    with pytest.raises(UnknownProductError):
        await get_unit_price(test_db, 999_999)


async def test_unknown_product_error_carries_product_id(test_db: AsyncSession) -> None:
    """UnknownProductError exposes the offending product_id for the ops log."""
    with pytest.raises(UnknownProductError) as exc:
        await get_unit_price(test_db, 424_242)
    assert exc.value.product_id == 424_242


async def test_get_unit_price_rejects_fractional(test_db: AsyncSession) -> None:
    """A fractional price is rejected loudly — money must not be silently truncated."""
    pid = await _make_product(test_db, price="100.50")
    with pytest.raises(ValueError, match="fractional"):
        await get_unit_price(test_db, pid)


@pytest.mark.parametrize("price", ["0", "-100", "-5.50"])
async def test_nonpositive_product_price_cannot_reach_cashback(
    test_db: AsyncSession, price: str
) -> None:
    """Block A rejects invalid prices at storage, before cashback can read them.

    The former fixtures inserted these values to exercise get_unit_price's
    defensive guard. products_sale_ck now makes that setup impossible.
    The positive lookup control remains test_get_unit_price_known_and_unknown.
    """
    with pytest.raises(IntegrityError) as exc:
        await _make_product(test_db, price=price)
    original = exc.value.orig.__cause__
    assert getattr(original, "constraint_name", None) == "products_sale_ck"
    await test_db.rollback()
    assert (await test_db.scalars(select(Product))).all() == []


# --------------------------------------------------------------------------- #
# point_ledger — balance, reconciliation, idempotency index, cascade
# --------------------------------------------------------------------------- #


async def test_balance_empty_is_zero(test_db: AsyncSession) -> None:
    """A user with no ledger rows has balance 0 (COALESCE, not NULL)."""
    user = User(telegram_id=40001, first_name="EmptyBal")
    test_db.add(user)
    await test_db.flush()
    assert await get_balance(test_db, user.id) == 0


async def test_balance_is_signed_sum(test_db: AsyncSession) -> None:
    """Balance = SUM(amount) over all reasons — earn positive, redeem negative."""
    user = User(telegram_id=40002, first_name="SignedSum")
    test_db.add(user)
    await test_db.flush()
    pid = await _make_product(test_db)
    qr_id = await _make_qr_id(test_db, pid)
    test_db.add_all(
        [
            _cashback_row(user.id, pid, qr_code_id=qr_id),  # +500
            PointLedger(
                user_id=user.id,
                amount=compute_cashback(10_000, 250),  # +250 welcome (same code, diff reason)
                reason=LedgerReason.WELCOME,
                qr_code_id=qr_id,
                product_id=pid,
                unit_price=10_000,
                rate_bps=250,
            ),
            PointLedger(user_id=user.id, amount=-300, reason=LedgerReason.REDEEM),  # -300
        ]
    )
    await test_db.flush()
    assert await get_balance(test_db, user.id) == 450


def test_earn_row_reconciles_pure() -> None:
    """earn_row_reconciles checks amount == compute_cashback(unit_price, rate_bps)."""
    good = _cashback_row(user_id=1, product_id=1, qr_code_id=2)
    assert earn_row_reconciles(good) is True

    tampered = _cashback_row(user_id=1, product_id=1, qr_code_id=2)
    tampered.amount = 999
    assert earn_row_reconciles(tampered) is False

    # non-earn row (no snapshot) — not applicable, treated as reconciled
    redeem = PointLedger(user_id=1, amount=-100, reason=LedgerReason.REDEEM)
    assert earn_row_reconciles(redeem) is True


async def test_partial_unique_blocks_duplicate_qr_reason(test_db: AsyncSession) -> None:
    """A second (qr_code_id, reason) earn row is rejected — the idempotency backstop."""
    user = User(telegram_id=40003, first_name="DupQr")
    test_db.add(user)
    await test_db.flush()
    pid = await _make_product(test_db)
    qr_id = await _make_qr_id(test_db, pid)
    test_db.add(_cashback_row(user.id, pid, qr_code_id=qr_id))
    await test_db.flush()

    test_db.add(_cashback_row(user.id, pid, qr_code_id=qr_id))  # same (qr, reason)
    try:
        with pytest.raises(IntegrityError):
            await test_db.flush()
    finally:
        await test_db.rollback()


async def test_partial_unique_allows_distinct_reason_and_null_qr(test_db: AsyncSession) -> None:
    """Same code with a different reason is allowed; NULL-qr rows are unconstrained."""
    user = User(telegram_id=40004, first_name="DistinctReason")
    test_db.add(user)
    await test_db.flush()
    pid = await _make_product(test_db)
    qr_id = await _make_qr_id(test_db, pid)
    test_db.add_all(
        [
            _cashback_row(user.id, pid, qr_code_id=qr_id),  # (qr, cashback)
            PointLedger(
                user_id=user.id,
                amount=compute_cashback(10_000, 250),
                reason=LedgerReason.WELCOME,  # (qr, welcome) — distinct reason, OK
                qr_code_id=qr_id,
                product_id=pid,
                unit_price=10_000,
                rate_bps=250,
            ),
            PointLedger(user_id=user.id, amount=-100, reason=LedgerReason.REDEEM),  # NULL qr
            PointLedger(user_id=user.id, amount=-50, reason=LedgerReason.EXPIRY),  # NULL qr
        ]
    )
    await test_db.flush()  # no IntegrityError
    assert await get_balance(test_db, user.id) == 600


async def test_ledger_cascades_on_user_delete(test_db: AsyncSession) -> None:
    """Deleting a user removes its ledger rows via DB FK CASCADE."""
    user = User(telegram_id=40005, first_name="Cascade")
    test_db.add(user)
    await test_db.flush()
    pid = await _make_product(test_db)
    qr_id = await _make_qr_id(test_db, pid)
    test_db.add(_cashback_row(user.id, pid, qr_code_id=qr_id))
    await test_db.flush()
    uid = user.id

    await test_db.delete(user)
    await test_db.flush()

    remaining = (
        (await test_db.execute(select(PointLedger).where(PointLedger.user_id == uid)))
        .scalars()
        .all()
    )
    assert remaining == []
