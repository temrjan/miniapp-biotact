"""Schema tests for the loyalty module (Stage 1 — consent table)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.loyalty.models import Consent, ConsentPurpose
from src.users.models import User


async def test_consent_table_roundtrips(test_db: AsyncSession) -> None:
    """create_all builds `consent`; insert/read works with defaults + enum."""
    user = User(telegram_id=10001, first_name="Test")
    test_db.add(user)
    await test_db.flush()

    test_db.add(
        Consent(
            user_id=user.id,
            purpose=ConsentPurpose.DATA_PROCESSING,
            version="v1",
            source="phone_share",
        )
    )
    await test_db.flush()

    row = (await test_db.execute(select(Consent).where(Consent.user_id == user.id))).scalar_one()
    assert row.purpose is ConsentPurpose.DATA_PROCESSING
    assert row.granted is True  # python-side default
    assert row.granted_at is not None  # server_default now()
    assert row.created_at is not None
    assert row.revoked_at is None
    assert row.source == "phone_share"


async def test_consent_revoked_state(test_db: AsyncSession) -> None:
    """A revoked consent stores granted=False for the given purpose."""
    user = User(telegram_id=10002, first_name="Rev")
    test_db.add(user)
    await test_db.flush()

    test_db.add(
        Consent(
            user_id=user.id,
            purpose=ConsentPurpose.MARKETING,
            granted=False,
            version="v2",
        )
    )
    await test_db.flush()

    row = (await test_db.execute(select(Consent).where(Consent.user_id == user.id))).scalar_one()
    assert row.purpose is ConsentPurpose.MARKETING
    assert row.granted is False


async def test_consent_cascades_on_user_delete(test_db: AsyncSession) -> None:
    """Deleting a user removes its consents via DB FK CASCADE (passive_deletes)."""
    user = User(telegram_id=10003, first_name="Cascade")
    test_db.add(user)
    await test_db.flush()
    test_db.add(Consent(user_id=user.id, purpose=ConsentPurpose.PERSONALIZATION, version="v1"))
    await test_db.flush()
    uid = user.id

    await test_db.delete(user)
    await test_db.flush()

    remaining = (
        (await test_db.execute(select(Consent).where(Consent.user_id == uid))).scalars().all()
    )
    assert remaining == []
