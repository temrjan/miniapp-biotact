"""Schema tests for the loyalty module (Stage 1 — consent + customer_profile + customer_event)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.loyalty.models import (
    Consent,
    ConsentPurpose,
    CustomerEvent,
    CustomerEventType,
    CustomerProfile,
    Gender,
    LoyaltyStatus,
)
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


async def test_customer_profile_server_defaults(test_db: AsyncSession) -> None:
    """A row inserted with ONLY user_id gets status=newbie / lifetime_activations=0.

    Raw INSERT bypasses the Python-side defaults so this proves the DB-level
    ``server_default`` — the same guarantee that lets lazily-created profiles for
    existing users land on ``newbie``/0 without a data migration.
    """
    user = User(telegram_id=20001, first_name="Defaults")
    test_db.add(user)
    await test_db.flush()

    await test_db.execute(
        text("INSERT INTO customer_profile (user_id) VALUES (:uid)"), {"uid": user.id}
    )

    row = (
        await test_db.execute(select(CustomerProfile).where(CustomerProfile.user_id == user.id))
    ).scalar_one()
    assert row.status is LoyaltyStatus.NEWBIE
    assert row.lifetime_activations == 0
    assert row.gender is None
    assert row.survey_raw is None
    assert row.created_at is not None  # server_default now()


async def test_customer_profile_full_roundtrip(test_db: AsyncSession) -> None:
    """All optional survey fields, gender enum and JSONB blob round-trip."""
    user = User(telegram_id=20002, first_name="Full")
    test_db.add(user)
    await test_db.flush()

    test_db.add(
        CustomerProfile(
            user_id=user.id,
            gender=Gender.FEMALE,
            age=34,
            city="Tashkent",
            purchase_goal="immunity",
            purchase_for_whom="child",
            status=LoyaltyStatus.BIOTACT_FRIEND,
            lifetime_activations=10,
            last_activation_at=datetime(2026, 6, 1, tzinfo=UTC),
            first_activated_product_id=77,
            registration_source="qr_activation",
            survey_raw={"q1": "a", "nested": {"k": 1}},
        )
    )
    await test_db.flush()

    row = (
        await test_db.execute(select(CustomerProfile).where(CustomerProfile.user_id == user.id))
    ).scalar_one()
    assert row.gender is Gender.FEMALE
    assert row.age == 34
    assert row.city == "Tashkent"
    assert row.status is LoyaltyStatus.BIOTACT_FRIEND
    assert row.lifetime_activations == 10
    assert row.first_activated_product_id == 77
    assert row.survey_raw == {"q1": "a", "nested": {"k": 1}}


async def test_customer_profile_is_one_to_one(test_db: AsyncSession) -> None:
    """The unique(user_id) constraint forbids a second profile per user."""
    user = User(telegram_id=20003, first_name="Unique")
    test_db.add(user)
    await test_db.flush()
    test_db.add(CustomerProfile(user_id=user.id))
    await test_db.flush()

    test_db.add(CustomerProfile(user_id=user.id))
    try:
        with pytest.raises(IntegrityError):
            await test_db.flush()
    finally:
        await test_db.rollback()


async def test_customer_profile_cascades_on_user_delete(test_db: AsyncSession) -> None:
    """Deleting a user removes its profile via DB FK CASCADE (passive_deletes)."""
    user = User(telegram_id=20004, first_name="Cascade")
    test_db.add(user)
    await test_db.flush()
    test_db.add(CustomerProfile(user_id=user.id))
    await test_db.flush()
    uid = user.id

    await test_db.delete(user)
    await test_db.flush()

    remaining = (
        (await test_db.execute(select(CustomerProfile).where(CustomerProfile.user_id == uid)))
        .scalars()
        .all()
    )
    assert remaining == []


async def test_user_phone_verified_at_nullable(test_db: AsyncSession) -> None:
    """New users.phone_verified_at column is nullable and round-trips."""
    user = User(telegram_id=20005, first_name="Phone")
    test_db.add(user)
    await test_db.flush()
    row = (await test_db.execute(select(User).where(User.id == user.id))).scalar_one()
    assert row.phone_verified_at is None

    row.phone_verified_at = datetime(2026, 6, 26, tzinfo=UTC)
    await test_db.flush()
    assert row.phone_verified_at is not None


async def test_customer_event_appends_and_roundtrips(test_db: AsyncSession) -> None:
    """create_all builds `customer_event`; a row with a JSONB payload round-trips.

    ``occurred_at``/``created_at`` come from the DB ``server_default now()``.
    """
    user = User(telegram_id=30001, first_name="Event")
    test_db.add(user)
    await test_db.flush()

    test_db.add(
        CustomerEvent(
            user_id=user.id,
            event_type=CustomerEventType.QR_ACTIVATED,
            payload={"sku": "BIFOLAK-STICK", "channel": "box"},
        )
    )
    await test_db.flush()

    row = (
        await test_db.execute(select(CustomerEvent).where(CustomerEvent.user_id == user.id))
    ).scalar_one()
    assert row.event_type == CustomerEventType.QR_ACTIVATED
    assert row.payload == {"sku": "BIFOLAK-STICK", "channel": "box"}
    assert row.occurred_at is not None  # server_default now()
    assert row.created_at is not None  # server_default now()


async def test_customer_event_payload_nullable(test_db: AsyncSession) -> None:
    """payload is optional — an event can be a bare type with no details."""
    user = User(telegram_id=30002, first_name="NoPayload")
    test_db.add(user)
    await test_db.flush()

    test_db.add(CustomerEvent(user_id=user.id, event_type=CustomerEventType.JOINED))
    await test_db.flush()

    row = (
        await test_db.execute(select(CustomerEvent).where(CustomerEvent.user_id == user.id))
    ).scalar_one()
    assert row.payload is None


async def test_customer_event_type_is_free_string(test_db: AsyncSession) -> None:
    """event_type is a free String, NOT a PG enum — an unknown type stores as-is.

    This guards the design decision: the vocabulary grows without a migration per
    new type, so a value outside :class:`CustomerEventType` must persist unchanged.
    """
    user = User(telegram_id=30003, first_name="FreeString")
    test_db.add(user)
    await test_db.flush()

    test_db.add(CustomerEvent(user_id=user.id, event_type="future_type_added_later"))
    await test_db.flush()

    row = (
        await test_db.execute(select(CustomerEvent).where(CustomerEvent.user_id == user.id))
    ).scalar_one()
    assert row.event_type == "future_type_added_later"


async def test_customer_event_is_append_only_many_per_user(test_db: AsyncSession) -> None:
    """No unique constraint — a user accumulates many timeline events."""
    user = User(telegram_id=30004, first_name="Timeline")
    test_db.add(user)
    await test_db.flush()

    test_db.add_all(
        [
            CustomerEvent(user_id=user.id, event_type=CustomerEventType.JOINED),
            CustomerEvent(user_id=user.id, event_type=CustomerEventType.PHONE_SHARED),
            CustomerEvent(user_id=user.id, event_type=CustomerEventType.QR_ACTIVATED),
        ]
    )
    await test_db.flush()

    count = (
        await test_db.execute(
            select(func.count()).select_from(CustomerEvent).where(CustomerEvent.user_id == user.id)
        )
    ).scalar_one()
    assert count == 3


async def test_customer_event_cascades_on_user_delete(test_db: AsyncSession) -> None:
    """Deleting a user removes its events via DB FK CASCADE (no ORM relationship)."""
    user = User(telegram_id=30005, first_name="Cascade")
    test_db.add(user)
    await test_db.flush()
    test_db.add(CustomerEvent(user_id=user.id, event_type=CustomerEventType.ORDER_PLACED))
    await test_db.flush()
    uid = user.id

    await test_db.delete(user)
    await test_db.flush()

    remaining = (
        (await test_db.execute(select(CustomerEvent).where(CustomerEvent.user_id == uid)))
        .scalars()
        .all()
    )
    assert remaining == []
