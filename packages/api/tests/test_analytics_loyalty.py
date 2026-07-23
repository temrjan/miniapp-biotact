"""Tests for the read-only loyalty monitoring endpoint (PR-D1): GET /api/v1/analytics/loyalty.

Seeding commits through a second sessionmaker on the same test DB (so the request/read
session sees committed rows under READ COMMITTED), mirroring test_loyalty_summary.py.
"""

import secrets
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest_asyncio
from httpx import AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.analytics.service import get_loyalty_stats
from src.config import settings
from src.loyalty.models import (
    CodeBatch,
    Consent,
    ConsentPurpose,
    CustomerEvent,
    CustomerEventType,
    CustomerProfile,
    Gender,
    LedgerReason,
    LoyaltyStatus,
    PointLedger,
    QrCode,
    QrCodeStatus,
)
from src.products.models import Product
from src.users.models import User

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"

LOYALTY_URL = f"{settings.api_v1_prefix}/analytics/loyalty"


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> object:
    """A sessionmaker on the same test DB ``test_db`` created — for committed seeding/reads."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _seed_rich(sm: async_sessionmaker[AsyncSession]) -> None:
    """Commit a comprehensive loyalty dataset so every LoyaltyStats field has a non-trivial value.

    See the per-block expectations asserted in ``test_loyalty_stats_aggregates_every_block``.
    """
    now = datetime.now(UTC)
    async with sm() as s, s.begin():
        # --- product + batch + 5 qr_codes (3 used, 2 generated) ---
        product = Product(name="Test BAD", slug=f"bad-{secrets.token_hex(4)}", price=Decimal("10000"))
        s.add(product)
        await s.flush()
        batch = CodeBatch(product_id=product.id, quantity=5, label="test-batch")
        s.add(batch)
        await s.flush()
        for i in range(3):
            s.add(
                QrCode(
                    code=f"used-{i}-{secrets.token_hex(3)}",
                    product_id=product.id,
                    batch_id=batch.id,
                    status=QrCodeStatus.USED.value,
                )
            )
        for i in range(2):
            s.add(
                QrCode(
                    code=f"gen-{i}-{secrets.token_hex(3)}",
                    product_id=product.id,
                    batch_id=batch.id,
                    status=QrCodeStatus.GENERATED.value,
                )
            )

        # --- users + profiles ---
        u1 = User(telegram_id=91001, first_name="U1", phone_verified_at=now)
        u2 = User(telegram_id=91002, first_name="U2", phone_verified_at=now)
        u3 = User(telegram_id=91003, first_name="U3")
        u4 = User(telegram_id=91004, first_name="U4")
        s.add_all([u1, u2, u3, u4])
        await s.flush()

        s.add_all(
            [
                CustomerProfile(
                    user_id=u1.id,
                    status=LoyaltyStatus.BIOTACT_FRIEND,
                    lifetime_activations=10,
                    gender=Gender.MALE,
                    age=30,
                    city="Tashkent",
                ),
                CustomerProfile(
                    user_id=u2.id, gender=Gender.FEMALE, age=22, city="Tashkent"
                ),
                CustomerProfile(user_id=u3.id, gender=Gender.MALE, age=50, city="Samarkand"),
                CustomerProfile(user_id=u4.id),  # no survey fields
            ]
        )

        # --- customer_event (funnel + windowed qr_activated + trend) ---
        def ev(user_id: int, event_type: CustomerEventType, occurred_at: datetime) -> CustomerEvent:
            return CustomerEvent(
                user_id=user_id, event_type=event_type.value, occurred_at=occurred_at
            )

        s.add_all(
            [
                ev(u1.id, CustomerEventType.JOINED, now),
                ev(u1.id, CustomerEventType.PHONE_SHARED, now),
                ev(u1.id, CustomerEventType.QR_ACTIVATED, now),  # 7d, 30d
                ev(u1.id, CustomerEventType.QR_ACTIVATED, now - timedelta(days=10)),  # 30d only
                ev(u1.id, CustomerEventType.SURVEY_DONE, now),
                ev(u2.id, CustomerEventType.JOINED, now),
                ev(u2.id, CustomerEventType.PHONE_SHARED, now),
                ev(u2.id, CustomerEventType.QR_ACTIVATED, now - timedelta(days=3)),  # 7d, 30d
                ev(u3.id, CustomerEventType.JOINED, now),
                ev(u3.id, CustomerEventType.PHONE_SHARED, now),
                ev(u3.id, CustomerEventType.QR_ACTIVATED, now - timedelta(days=40)),  # outside 30d
                ev(u4.id, CustomerEventType.JOINED, now),
            ]
        )

        # --- consent (append-style: u3 granted THEN revoked → still "ever granted") ---
        def consent(user_id: int, granted: bool) -> Consent:
            return Consent(
                user_id=user_id,
                purpose=ConsentPurpose.MARKETING,
                granted=granted,
                version="1.0",
            )

        s.add_all(
            [
                consent(u1.id, True),
                consent(u2.id, True),
                consent(u3.id, True),
                consent(u3.id, False),  # later revoke — u3 still counts in ever_granted
            ]
        )

        # --- point_ledger (+earn / -redeem) ---
        s.add_all(
            [
                PointLedger(user_id=u1.id, amount=5000, reason=LedgerReason.CASHBACK.value),
                PointLedger(user_id=u1.id, amount=2500, reason=LedgerReason.WELCOME.value),
                PointLedger(user_id=u2.id, amount=3000, reason=LedgerReason.CASHBACK.value),
                PointLedger(user_id=u1.id, amount=-1000, reason=LedgerReason.REDEEM.value),
            ]
        )


# --------------------------------------------------------------------------- #
# service: aggregation over every block
# --------------------------------------------------------------------------- #


async def test_loyalty_stats_aggregates_every_block(test_db: AsyncSession, maker) -> None:
    """Rich seed → each block reports the exact expected numbers (incl. friends == 1)."""
    await _seed_rich(maker)
    async with maker() as s:
        stats = await get_loyalty_stats(s, days=30)

    # Block 1 — activations
    assert stats.qr_generated == 5
    assert stats.qr_activated == 3  # qr_codes.status == 'used'
    assert stats.qr_activated_7d == 2  # events: now, now-3d
    assert stats.qr_activated_30d == 3  # + now-10d; now-40d excluded
    assert stats.activation_rate == 60.0  # 3 / 5 * 100
    assert stats.batches_count == 1
    assert len(stats.activations_by_day) == 31  # days + 1
    assert sum(p.count for p in stats.activations_by_day) == 3  # now, now-3d, now-10d

    # Block 2 — members
    assert stats.profiles_total == 4
    assert stats.phone_verified == 2
    assert stats.friends == 1  # ONLY the BIOTACT_FRIEND profile (enum-member filter)

    # Block 3 — funnel (distinct users per step, all time)
    funnel = {step.step: step.users for step in stats.funnel}
    assert funnel == {"joined": 4, "phone_shared": 3, "qr_activated": 3, "survey_done": 1}
    assert stats.consents_ever_granted == 3  # u1, u2, u3 (u3 revoked later but ever-granted)

    # Block 4 — points
    assert stats.points_earned == 10500  # 5000 + 2500 + 3000
    assert stats.points_spent == 1000  # shown positive
    assert stats.points_outstanding == 9500  # 10500 - 1000
    by_reason = {r.reason: r.total for r in stats.points_by_reason}
    assert by_reason == {"cashback": 8000, "welcome": 2500, "redeem": -1000}

    # Block 5 — demographics
    demo = stats.demographics
    assert {g.gender: g.count for g in demo.gender} == {"male": 2, "female": 1, "unknown": 1}
    assert {b.bucket: b.count for b in demo.age_buckets} == {
        "18-24": 1,
        "25-34": 1,
        "45-54": 1,
        "unknown": 1,
    }
    assert {c.city: c.count for c in demo.top_cities} == {"Tashkent": 2, "Samarkand": 1}
    assert demo.survey_coverage == 75.0  # 3 of 4 profiles have >=1 survey field


async def test_loyalty_stats_empty_db_is_all_zero_not_error(test_db: AsyncSession, maker) -> None:
    """Current prod state (empty loyalty tables) → zeros, guarded /0, no exception."""
    async with maker() as s:
        stats = await get_loyalty_stats(s, days=30)

    assert stats.qr_generated == 0
    assert stats.qr_activated == 0
    assert stats.activation_rate == 0.0  # guard: generated == 0, no ZeroDivisionError
    assert stats.batches_count == 0
    assert len(stats.activations_by_day) == 31
    assert all(p.count == 0 for p in stats.activations_by_day)

    assert stats.profiles_total == 0
    assert stats.phone_verified == 0
    assert stats.friends == 0

    # funnel always lists the 4 steps, each zero
    assert {s.step: s.users for s in stats.funnel} == {
        "joined": 0,
        "phone_shared": 0,
        "qr_activated": 0,
        "survey_done": 0,
    }
    assert stats.consents_ever_granted == 0

    assert stats.points_earned == 0
    assert stats.points_spent == 0
    assert stats.points_outstanding == 0
    assert stats.points_by_reason == []

    demo = stats.demographics
    assert demo.gender == []
    assert demo.age_buckets == []
    assert demo.top_cities == []
    assert demo.survey_coverage == 0.0  # guard: profiles_total == 0


async def test_loyalty_stats_trend_fills_gaps_with_zero(test_db: AsyncSession, maker) -> None:
    """activations_by_day spans the whole `days` window; only seeded days are non-zero."""
    now = datetime.now(UTC)
    async with maker() as s, s.begin():
        u = User(telegram_id=92001, first_name="Trend")
        s.add(u)
        await s.flush()
        s.add_all(
            [
                CustomerEvent(
                    user_id=u.id, event_type=CustomerEventType.QR_ACTIVATED.value, occurred_at=now
                ),
                CustomerEvent(
                    user_id=u.id,
                    event_type=CustomerEventType.QR_ACTIVATED.value,
                    occurred_at=now - timedelta(days=2),
                ),
            ]
        )

    async with maker() as s:
        stats = await get_loyalty_stats(s, days=7)

    assert len(stats.activations_by_day) == 8  # days + 1
    non_zero = [p for p in stats.activations_by_day if p.count != 0]
    assert sum(p.count for p in stats.activations_by_day) == 2
    assert len(non_zero) == 2  # gaps between/around the two seeded days are filled with 0
    assert all(p.count == 1 for p in non_zero)


# --------------------------------------------------------------------------- #
# HTTP endpoint — authorization (mirrors require_admin)
# --------------------------------------------------------------------------- #


async def test_loyalty_endpoint_requires_bearer(client: AsyncClient) -> None:
    """No Authorization header → 401."""
    resp = await client.get(LOYALTY_URL)
    assert resp.status_code == 401


async def test_loyalty_endpoint_rejects_non_admin_token(client: AsyncClient) -> None:
    """Valid JWT but type != 'admin' → 403."""
    token = jwt.encode(
        {"sub": "1", "type": "user", "exp": int(time.time()) + 3600},
        settings.secret_key.get_secret_value(),
        algorithm=settings.algorithm,
    )
    resp = await client.get(LOYALTY_URL, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


async def test_loyalty_endpoint_admin_gets_200(
    client: AsyncClient, maker, admin_headers: dict[str, str]
) -> None:
    """Admin JWT (sub=1) + committed admin user id=1 → 200 with the loyalty snapshot shape."""
    async with maker() as s, s.begin():
        s.add(User(id=1, telegram_id=90001, first_name="Admin", is_admin=True))

    resp = await client.get(LOYALTY_URL, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # shape sanity — empty program still returns a fully-formed snapshot
    assert body["qr_generated"] == 0
    assert body["activation_rate"] == 0.0
    assert {s["step"] for s in body["funnel"]} == {
        "joined",
        "phone_shared",
        "qr_activated",
        "survey_done",
    }
    assert body["demographics"]["survey_coverage"] == 0.0
