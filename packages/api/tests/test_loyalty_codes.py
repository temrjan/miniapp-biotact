"""Tests for QR code generation, batches, and the point_ledger FKs (Stage 2)."""

import secrets
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.loyalty.codes import codes_to_csv, generate_batch, generate_code
from src.loyalty.models import LedgerReason, PointLedger, QrCode, QrCodeStatus
from src.loyalty.pricing import UnknownProductError
from src.products.models import Product
from src.users.models import User

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


async def _make_product(session: AsyncSession, *, price: str = "10000") -> int:
    """Seed a priced product; return its id (the cashback key)."""
    product = Product(
        name="Test BAD", slug=f"test-bad-{secrets.token_hex(4)}", price=Decimal(price)
    )
    session.add(product)
    await session.flush()
    return product.id


# --------------------------------------------------------------------------- #
# code generation — pure
# --------------------------------------------------------------------------- #


def test_generate_code_is_opaque_crockford_base32() -> None:
    """A code is 12 Crockford base32 chars (no ambiguous I/L/O/U)."""
    code = generate_code()
    assert len(code) == 12
    assert all(ch in _CROCKFORD for ch in code)
    assert not any(ch in "ILOU" for ch in code)


def test_generate_code_is_random() -> None:
    """Two codes differ (sanity that it is not constant)."""
    assert generate_code() != generate_code()


def test_codes_to_csv_format() -> None:
    """CSV is ``code,product_id`` with a header row."""
    codes = [QrCode(code="ABC", product_id=1), QrCode(code="DEF", product_id=2)]
    lines = codes_to_csv(codes).strip().splitlines()
    assert lines == ["code,product_id", "ABC,1", "DEF,2"]


# --------------------------------------------------------------------------- #
# batch generation — DB
# --------------------------------------------------------------------------- #


async def test_generate_batch_creates_unique_generated_codes(test_db: AsyncSession) -> None:
    """A batch yields N unique codes, all status=generated, bound to the product+batch."""
    pid = await _make_product(test_db)
    batch, codes = await generate_batch(test_db, pid, 5)
    assert len(codes) == 5
    assert len({c.code for c in codes}) == 5
    assert all(c.status == QrCodeStatus.GENERATED for c in codes)
    assert all(c.product_id == pid for c in codes)
    assert all(c.batch_id == batch.id for c in codes)
    assert all(c.activated_by is None and c.used_at is None for c in codes)


async def test_generate_batch_rejects_unpriced_product(test_db: AsyncSession) -> None:
    """MINOR-2a: a non-existent / unpriced product is refused at generation (fail-fast)."""
    with pytest.raises(UnknownProductError):
        await generate_batch(test_db, 999_999, 3)


async def test_generate_batch_rejects_nonpositive_quantity(test_db: AsyncSession) -> None:
    """quantity must be >= 1."""
    pid = await _make_product(test_db)
    with pytest.raises(ValueError, match="quantity"):
        await generate_batch(test_db, pid, 0)


async def test_generate_batch_codes_globally_unique(test_db: AsyncSession) -> None:
    """Codes are unique across batches (the DB pre-check, backstopped by UNIQUE)."""
    pid = await _make_product(test_db)
    _, codes1 = await generate_batch(test_db, pid, 10)
    _, codes2 = await generate_batch(test_db, pid, 10)
    assert len({c.code for c in codes1} | {c.code for c in codes2}) == 20


async def test_qr_code_unique_constraint(test_db: AsyncSession) -> None:
    """UNIQUE(code) blocks a duplicate code — the hard guard behind the pre-check."""
    pid = await _make_product(test_db)
    batch, codes = await generate_batch(test_db, pid, 1)
    test_db.add(QrCode(code=codes[0].code, product_id=pid, batch_id=batch.id))
    try:
        with pytest.raises(IntegrityError):
            await test_db.flush()
    finally:
        await test_db.rollback()


# --------------------------------------------------------------------------- #
# point_ledger.qr_code_id FK → qr_codes ; RESTRICT money guards
# --------------------------------------------------------------------------- #


async def test_ledger_fk_rejects_missing_qr_code(test_db: AsyncSession) -> None:
    """An earn row referencing a non-existent qr_code_id is rejected by the FK."""
    user = User(telegram_id=41001, first_name="FkMissing")
    test_db.add(user)
    await test_db.flush()
    pid = await _make_product(test_db)
    test_db.add(
        PointLedger(
            user_id=user.id,
            amount=500,
            reason=LedgerReason.CASHBACK,
            qr_code_id=999_999,  # no such qr_code
            product_id=pid,
            unit_price=10_000,
            rate_bps=500,
        )
    )
    try:
        with pytest.raises(IntegrityError):
            await test_db.flush()
    finally:
        await test_db.rollback()


async def test_ledger_fk_accepts_existing_qr_code(test_db: AsyncSession) -> None:
    """An earn row referencing a real qr_code is accepted."""
    user = User(telegram_id=41002, first_name="FkOk")
    test_db.add(user)
    await test_db.flush()
    pid = await _make_product(test_db)
    _, codes = await generate_batch(test_db, pid, 1)
    test_db.add(
        PointLedger(
            user_id=user.id,
            amount=500,
            reason=LedgerReason.CASHBACK,
            qr_code_id=codes[0].id,
            product_id=pid,
            unit_price=10_000,
            rate_bps=500,
        )
    )
    await test_db.flush()  # no error


async def test_qr_code_with_ledger_cannot_be_deleted(test_db: AsyncSession) -> None:
    """RESTRICT invariant: a qr_code that accrued points cannot be deleted (money guard)."""
    user = User(telegram_id=41003, first_name="RestrictQr")
    test_db.add(user)
    await test_db.flush()
    pid = await _make_product(test_db)
    _, codes = await generate_batch(test_db, pid, 1)
    qr = codes[0]
    test_db.add(
        PointLedger(
            user_id=user.id,
            amount=500,
            reason=LedgerReason.CASHBACK,
            qr_code_id=qr.id,
            product_id=pid,
            unit_price=10_000,
            rate_bps=500,
        )
    )
    await test_db.flush()

    await test_db.delete(qr)  # RESTRICT blocks while a ledger row references it
    try:
        with pytest.raises(IntegrityError):
            await test_db.flush()
    finally:
        await test_db.rollback()


async def test_product_with_codes_cannot_be_deleted(test_db: AsyncSession) -> None:
    """RESTRICT invariant: a product with QR codes / ledger cannot be deleted (money guard).

    Regression guard — a future migration weakening qr_codes.product_id / point_ledger.product_id
    to CASCADE would orphan money truth; this fails loudly if that happens.
    """
    pid = await _make_product(test_db)
    await generate_batch(test_db, pid, 1)  # qr_codes.product_id RESTRICT references the product
    product = await test_db.get(Product, pid)

    await test_db.delete(product)
    try:
        with pytest.raises(IntegrityError):
            await test_db.flush()
    finally:
        await test_db.rollback()
