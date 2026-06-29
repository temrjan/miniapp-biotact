"""Tests for QR code generation, batches, and the point_ledger FK (Stage 2 PR-2.2)."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.loyalty.codes import codes_to_csv, generate_batch, generate_code
from src.loyalty.models import LedgerReason, PointLedger, QrCode, QrCodeStatus
from src.loyalty.pricing import SENTINEL_SKU, UnknownSkuError
from src.users.models import User

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


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
    """CSV is ``code,sku`` with a header row."""
    codes = [QrCode(code="ABC", sku="S1"), QrCode(code="DEF", sku="S2")]
    lines = codes_to_csv(codes).strip().splitlines()
    assert lines == ["code,sku", "ABC,S1", "DEF,S2"]


# --------------------------------------------------------------------------- #
# batch generation — DB
# --------------------------------------------------------------------------- #


async def test_generate_batch_creates_unique_generated_codes(test_db: AsyncSession) -> None:
    """A batch yields N unique codes, all status=generated, SKU set, linked to the batch."""
    batch, codes = await generate_batch(test_db, SENTINEL_SKU, 5)
    assert len(codes) == 5
    assert len({c.code for c in codes}) == 5
    assert all(c.status == QrCodeStatus.GENERATED for c in codes)
    assert all(c.sku == SENTINEL_SKU for c in codes)
    assert all(c.batch_id == batch.id for c in codes)
    assert all(c.activated_by is None and c.used_at is None for c in codes)


async def test_generate_batch_rejects_unpriced_sku(test_db: AsyncSession) -> None:
    """MINOR-2a: a SKU with no cashback price is refused at generation (fail-fast)."""
    with pytest.raises(UnknownSkuError):
        await generate_batch(test_db, "NOT-PRICED-SKU", 3)


async def test_generate_batch_rejects_nonpositive_quantity(test_db: AsyncSession) -> None:
    """quantity must be >= 1."""
    with pytest.raises(ValueError, match="quantity"):
        await generate_batch(test_db, SENTINEL_SKU, 0)


async def test_generate_batch_codes_globally_unique(test_db: AsyncSession) -> None:
    """Codes are unique across batches (the DB pre-check, backstopped by UNIQUE)."""
    _, codes1 = await generate_batch(test_db, SENTINEL_SKU, 10)
    _, codes2 = await generate_batch(test_db, SENTINEL_SKU, 10)
    assert len({c.code for c in codes1} | {c.code for c in codes2}) == 20


async def test_qr_code_unique_constraint(test_db: AsyncSession) -> None:
    """UNIQUE(code) blocks a duplicate code — the hard guard behind the pre-check."""
    batch, codes = await generate_batch(test_db, SENTINEL_SKU, 1)
    test_db.add(QrCode(code=codes[0].code, sku=SENTINEL_SKU, batch_id=batch.id))
    try:
        with pytest.raises(IntegrityError):
            await test_db.flush()
    finally:
        await test_db.rollback()


# --------------------------------------------------------------------------- #
# point_ledger.qr_code_id FK → qr_codes (added in this PR)
# --------------------------------------------------------------------------- #


async def test_ledger_fk_rejects_missing_qr_code(test_db: AsyncSession) -> None:
    """An earn row referencing a non-existent qr_code_id is rejected by the FK."""
    user = User(telegram_id=41001, first_name="FkMissing")
    test_db.add(user)
    await test_db.flush()
    test_db.add(
        PointLedger(
            user_id=user.id,
            amount=500,
            reason=LedgerReason.CASHBACK,
            qr_code_id=999_999,  # no such qr_code
            sku=SENTINEL_SKU,
            unit_price=1,
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
    _, codes = await generate_batch(test_db, SENTINEL_SKU, 1)
    test_db.add(
        PointLedger(
            user_id=user.id,
            amount=500,
            reason=LedgerReason.CASHBACK,
            qr_code_id=codes[0].id,
            sku=SENTINEL_SKU,
            unit_price=1,
            rate_bps=500,
        )
    )
    await test_db.flush()  # no error


async def test_qr_code_with_ledger_cannot_be_deleted(test_db: AsyncSession) -> None:
    """RESTRICT invariant: a qr_code that accrued points cannot be deleted (money guard).

    Regression guard — a future migration weakening this FK to CASCADE/SET NULL would
    let money truth be orphaned; this test fails loudly if that ever happens.
    """
    user = User(telegram_id=41003, first_name="RestrictGuard")
    test_db.add(user)
    await test_db.flush()
    _, codes = await generate_batch(test_db, SENTINEL_SKU, 1)
    qr = codes[0]
    test_db.add(
        PointLedger(
            user_id=user.id,
            amount=500,
            reason=LedgerReason.CASHBACK,
            qr_code_id=qr.id,
            sku=SENTINEL_SKU,
            unit_price=1,
            rate_bps=500,
        )
    )
    await test_db.flush()

    await test_db.delete(qr)  # RESTRICT must block this while a ledger row references it
    try:
        with pytest.raises(IntegrityError):
            await test_db.flush()
    finally:
        await test_db.rollback()
