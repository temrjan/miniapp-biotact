"""Tests for the read-only loyalty summary endpoint (Stage 3, PR-3.2): GET /api/v1/loyalty/me.

Seeding commits through a second sessionmaker on the same test DB (so the endpoint's request
session sees committed rows under READ COMMITTED), mirroring test_loyalty_activation.py.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.loyalty.models import CustomerProfile, Gender, LedgerReason, LoyaltyStatus, PointLedger
from src.loyalty.service import InsufficientPointsError, get_loyalty_summary, reserve_points
from src.orders.models import Order, OrderStatus
from src.orders.service import REDEEM_RESERVE_TIMEOUT
from src.users.models import User
from tests.conftest import _make_init_data

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> object:
    """A sessionmaker on the same test DB ``test_db`` created — for committed seeding/asserts."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _seed_user(sm: async_sessionmaker[AsyncSession], telegram_id: int) -> int:
    """Commit a bare user (no profile); return its id."""
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Seed")
        s.add(user)
        await s.flush()
        return user.id


async def _seed_profile(
    sm: async_sessionmaker[AsyncSession],
    telegram_id: int,
    *,
    lifetime: int,
    status: LoyaltyStatus = LoyaltyStatus.NEWBIE,
    balance: int = 0,
    gender: Gender | None = None,
    age: int | None = None,
    city: str | None = None,
) -> int:
    """Commit a user + profile (lifetime/status/survey), optionally a ledger row for a balance."""
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Seed")
        s.add(user)
        await s.flush()
        s.add(
            CustomerProfile(
                user_id=user.id,
                lifetime_activations=lifetime,
                status=status,
                gender=gender,
                age=age,
                city=city,
            )
        )
        if balance:
            s.add(PointLedger(user_id=user.id, amount=balance, reason=LedgerReason.CASHBACK))
        return user.id


# --------------------------------------------------------------------------- #
# service: read-only summary + defaults, no side effects
# --------------------------------------------------------------------------- #


async def test_summary_unknown_user_is_newbie_default_and_writes_nothing(
    test_db: AsyncSession, maker
) -> None:
    """An unknown telegram_id → newbie default, and NO user/profile row is created (GET = read)."""
    async with maker() as s:
        summary = await get_loyalty_summary(s, 70001)
    assert summary.tier is LoyaltyStatus.NEWBIE
    assert summary.balance == 0
    assert summary.lifetime_activations == 0
    assert summary.activations_to_friend == 10
    assert summary.is_member is False  # unknown user has no profile
    async with maker() as s:
        users = (await s.execute(select(func.count()).select_from(User))).scalar_one()
        profiles = (await s.execute(select(func.count()).select_from(CustomerProfile))).scalar_one()
    assert users == 0
    assert profiles == 0


async def test_summary_user_without_profile_is_default(test_db: AsyncSession, maker) -> None:
    """A known user with no profile yet → newbie default, balance 0, no profile created."""
    await _seed_user(maker, 70002)
    async with maker() as s:
        summary = await get_loyalty_summary(s, 70002)
    assert summary.tier is LoyaltyStatus.NEWBIE
    assert summary.balance == 0
    assert summary.lifetime_activations == 0
    assert summary.activations_to_friend == 10
    assert summary.is_member is False  # user exists but has no profile → not a member
    async with maker() as s:
        profiles = (await s.execute(select(func.count()).select_from(CustomerProfile))).scalar_one()
    assert profiles == 0  # GET did not lazily create a profile


async def test_summary_newbie_reports_progress_and_balance(test_db: AsyncSession, maker) -> None:
    """A newbie with 3 activations → tier newbie, 7 remaining, balance from the ledger."""
    await _seed_profile(maker, 70003, lifetime=3, balance=1500)
    async with maker() as s:
        summary = await get_loyalty_summary(s, 70003)
    assert summary.tier is LoyaltyStatus.NEWBIE
    assert summary.lifetime_activations == 3
    assert summary.activations_to_friend == 7
    assert summary.balance == 1500
    assert summary.is_member is True  # profile row exists → member


async def test_summary_friend_reports_zero_remaining(test_db: AsyncSession, maker) -> None:
    """A friend → tier friend, 0 remaining regardless of count, balance from the ledger."""
    await _seed_profile(
        maker, 70004, lifetime=12, status=LoyaltyStatus.BIOTACT_FRIEND, balance=9000
    )
    async with maker() as s:
        summary = await get_loyalty_summary(s, 70004)
    assert summary.tier is LoyaltyStatus.BIOTACT_FRIEND
    assert summary.activations_to_friend == 0
    assert summary.balance == 9000
    assert summary.is_member is True  # profile row exists → member


async def test_summary_newbie_past_threshold_clamps_to_zero(test_db: AsyncSession, maker) -> None:
    """A newbie whose counter is already ≥10 (backfill) → remaining clamps to 0, never negative."""
    await _seed_profile(maker, 70005, lifetime=12, status=LoyaltyStatus.NEWBIE)
    async with maker() as s:
        summary = await get_loyalty_summary(s, 70005)
    assert summary.tier is LoyaltyStatus.NEWBIE
    assert summary.lifetime_activations == 12
    assert summary.activations_to_friend == 0  # max(0, 10 - 12) guard — not -2


async def test_summary_echoes_saved_survey_fields(test_db: AsyncSession, maker) -> None:
    """A member who saved the survey → the summary echoes gender/age/city exactly as stored."""
    await _seed_profile(maker, 70006, lifetime=1, gender=Gender.MALE, age=47, city="Ташкент")
    async with maker() as s:
        summary = await get_loyalty_summary(s, 70006)
    assert summary.gender is Gender.MALE
    assert summary.age == 47
    assert summary.city == "Ташкент"


async def test_summary_member_without_survey_has_null_survey_fields(
    test_db: AsyncSession, maker
) -> None:
    """A member who never saved the survey → all three survey fields are None (mini-app asks)."""
    await _seed_profile(maker, 70007, lifetime=1)
    async with maker() as s:
        summary = await get_loyalty_summary(s, 70007)
    assert summary.is_member is True
    assert (summary.gender, summary.age, summary.city) == (None, None, None)


async def test_summary_non_member_has_null_survey_fields(test_db: AsyncSession, maker) -> None:
    """Both non-member states (unknown user / bare user without profile) → survey fields None."""
    await _seed_user(maker, 70008)
    async with maker() as s:
        unknown = await get_loyalty_summary(s, 79999)
        bare = await get_loyalty_summary(s, 70008)
    assert unknown.is_member is False
    assert (unknown.gender, unknown.age, unknown.city) == (None, None, None)
    assert bare.is_member is False
    assert (bare.gender, bare.age, bare.city) == (None, None, None)


# --------------------------------------------------------------------------- #
# HTTP endpoint — dark gate, auth, end-to-end
# --------------------------------------------------------------------------- #


async def test_me_dark_returns_404(client) -> None:
    """loyalty_enabled off (default) → 404 before auth (dark, not probeable)."""
    resp = await client.get("/api/v1/loyalty/me")
    assert resp.status_code == 404


async def test_me_requires_init_data(client, monkeypatch) -> None:
    """Flag on but no X-Init-Data → 401."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    resp = await client.get("/api/v1/loyalty/me")
    assert resp.status_code == 401


async def test_me_happy_e2e_friend(client, maker, monkeypatch) -> None:
    """Full path: signed initData + flag on → 200 with the friend snapshot."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    await _seed_profile(
        maker, 70010, lifetime=10, status=LoyaltyStatus.BIOTACT_FRIEND, balance=5800
    )
    headers = {
        "X-Init-Data": _make_init_data("test-bot-token-123", {"id": 70010, "first_name": "Friend"})
    }
    resp = await client.get("/api/v1/loyalty/me", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tier"] == "biotact_friend"
    assert body["balance"] == 5800
    assert body["lifetime_activations"] == 10
    assert body["activations_to_friend"] == 0
    assert body["is_member"] is True


async def test_me_happy_e2e_unknown_user_default(client, monkeypatch) -> None:
    """Signed initData for a user not in the DB + flag on → 200 newbie default."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    headers = {
        "X-Init-Data": _make_init_data("test-bot-token-123", {"id": 70011, "first_name": "Fresh"})
    }
    resp = await client.get("/api/v1/loyalty/me", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tier"] == "newbie"
    assert body["balance"] == 0
    assert body["activations_to_friend"] == 10
    assert body["is_member"] is False


async def test_me_happy_e2e_echoes_survey(client, maker, monkeypatch) -> None:
    """HTTP: a member with a saved survey → 200 body carries gender/age/city verbatim."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    await _seed_profile(maker, 70012, lifetime=2, gender=Gender.FEMALE, age=30, city="Самарканд")
    headers = {
        "X-Init-Data": _make_init_data("test-bot-token-123", {"id": 70012, "first_name": "Survey"})
    }
    resp = await client.get("/api/v1/loyalty/me", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["gender"] == "female"  # StrEnum VALUE on the wire — what the mini-app types expect
    assert body["age"] == 30
    assert body["city"] == "Самарканд"


async def test_me_echoes_out_of_range_stored_age_verbatim(client, maker, monkeypatch) -> None:
    """A stored age outside the write-side 1..120 bound (no DB CHECK) still echoes as 200.

    Locks the deliberate design decision on ``LoyaltySummaryResponse``: the response is an
    UNCONSTRAINED mirror of the DB — re-adding ``SurveyAge`` bounds there would 500 this member.
    """
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    await _seed_profile(maker, 70013, lifetime=1, age=200)
    headers = {
        "X-Init-Data": _make_init_data("test-bot-token-123", {"id": 70013, "first_name": "Legacy"})
    }
    resp = await client.get("/api/v1/loyalty/me", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["age"] == 200


# --------------------------------------------------------------------------- #
# Model-1 reservations: `available` is what the money path would actually admit
#
# The defect these lock (prod, 2026-07-23): the profile showed SUM(ledger) while `reserve_points`
# admitted against SUM(ledger) − reservations, so a customer whose card leg was abandoned saw their
# points "still there" and was then told "not enough points" on the next attempt.
# --------------------------------------------------------------------------- #


async def _seed_reserve(
    sm: async_sessionmaker[AsyncSession],
    *,
    user_id: int,
    points_applied: int,
    age: timedelta,
    payment_status: str = "unpaid",
    tag: str = "R",
) -> tuple[int, datetime]:
    """Commit an order that reserves ``points_applied``, aged ``age``; return (id, created_at).

    ``created_at`` overrides the server default so a test can place the reservation either side of
    :data:`~src.orders.service.REDEEM_RESERVE_TIMEOUT`.
    """
    async with sm() as session, session.begin():
        order = Order(
            order_number=f"ORD-{tag}-{user_id}-{int(age.total_seconds())}",
            user_id=user_id,
            subtotal=Decimal("69000.00"),
            delivery_fee=Decimal("0"),
            total=Decimal("69000.00"),
            points_applied=points_applied,
            payment_status=payment_status,
            status=OrderStatus.PENDING,
            created_at=datetime.now(UTC) - age,
        )
        session.add(order)
        await session.flush()
        return order.id, order.created_at


async def test_summary_without_reservations_reports_full_balance_as_available(maker) -> None:
    """Nothing pinned → `available` equals `balance` and there is no expiry to report."""
    await _seed_profile(maker, 70020, lifetime=1, balance=20_000)
    async with maker() as s:
        summary = await get_loyalty_summary(s, 70020)
    assert summary.balance == 20_000
    assert summary.available == 20_000
    assert summary.reserved_until is None


async def test_summary_subtracts_live_reservation_and_reports_its_expiry(maker) -> None:
    """A live reservation is withheld from `available`, and `reserved_until` says when it lapses."""
    user_id = await _seed_profile(maker, 70021, lifetime=1, balance=20_000)
    _, created_at = await _seed_reserve(
        maker, user_id=user_id, points_applied=17_300, age=timedelta(minutes=1)
    )
    async with maker() as s:
        summary = await get_loyalty_summary(s, 70021)
    assert summary.balance == 20_000  # the points are still the customer's — not burned
    assert summary.available == 2_700  # but this is all a redemption would be admitted for
    assert summary.reserved_until == created_at + REDEEM_RESERVE_TIMEOUT
    # Timezone-aware, always: a naive value here would repeat the live `paid_at` −2h defect once
    # this timestamp reaches a customer-facing string.
    assert summary.reserved_until is not None
    assert summary.reserved_until.tzinfo is not None


async def test_summary_ignores_reservation_past_its_timeout(maker) -> None:
    """An expired reservation stops counting: the next attempt is what reclaims it, and it will.

    Holding it against `available` would have the screen forbid what the server already allows —
    `expire_stale_partials` runs before `reserve_points` on the very next redeem attempt.
    """
    user_id = await _seed_profile(maker, 70022, lifetime=1, balance=20_000)
    await _seed_reserve(
        maker,
        user_id=user_id,
        points_applied=17_300,
        age=REDEEM_RESERVE_TIMEOUT + timedelta(minutes=1),
    )
    async with maker() as s:
        summary = await get_loyalty_summary(s, 70022)
    assert summary.available == 20_000
    assert summary.reserved_until is None


async def test_summary_counts_only_the_live_one_when_reservations_are_mixed(maker) -> None:
    """One live + one expired → only the live one is withheld, and it sets the expiry."""
    user_id = await _seed_profile(maker, 70023, lifetime=1, balance=20_000)
    _, live_at = await _seed_reserve(
        maker, user_id=user_id, points_applied=5_000, age=timedelta(minutes=2), tag="LIVE"
    )
    await _seed_reserve(
        maker,
        user_id=user_id,
        points_applied=9_000,
        age=REDEEM_RESERVE_TIMEOUT + timedelta(minutes=5),
        tag="STALE",
    )
    async with maker() as s:
        summary = await get_loyalty_summary(s, 70023)
    assert summary.available == 15_000  # 20 000 − 5 000, the expired 9 000 is not held
    assert summary.reserved_until == live_at + REDEEM_RESERVE_TIMEOUT


async def test_summary_sums_two_live_reservations_and_reports_the_earliest_expiry(maker) -> None:
    """Two live reservations: both are withheld, and the EARLIEST one sets `reserved_until`.

    Nothing stops a customer abandoning one payment and starting another. Reporting the later
    expiry would tell them to wait longer than they actually must.
    """
    user_id = await _seed_profile(maker, 70024, lifetime=1, balance=20_000)
    _, older_at = await _seed_reserve(
        maker, user_id=user_id, points_applied=5_000, age=timedelta(minutes=20), tag="OLDER"
    )
    _, newer_at = await _seed_reserve(
        maker, user_id=user_id, points_applied=3_000, age=timedelta(minutes=2), tag="NEWER"
    )
    async with maker() as s:
        summary = await get_loyalty_summary(s, 70024)
    assert summary.available == 12_000  # both held: 20 000 − 5 000 − 3 000
    assert summary.reserved_until == older_at + REDEEM_RESERVE_TIMEOUT
    assert summary.reserved_until != newer_at + REDEEM_RESERVE_TIMEOUT


async def test_summary_does_not_treat_a_paid_order_as_a_reservation(maker) -> None:
    """A settled order pins nothing — its points were already debited by `commit_redeem`."""
    user_id = await _seed_profile(maker, 70025, lifetime=1, balance=20_000)
    await _seed_reserve(
        maker,
        user_id=user_id,
        points_applied=17_300,
        age=timedelta(minutes=1),
        payment_status="paid",
    )
    async with maker() as s:
        summary = await get_loyalty_summary(s, 70025)
    assert summary.available == 20_000
    assert summary.reserved_until is None


async def test_summary_for_a_user_without_a_profile_reports_zero_available(maker) -> None:
    """No profile → `available` 0, mirroring `reserve_points`, which refuses without a lock row."""
    await _seed_user(maker, 70026)
    async with maker() as s:
        summary = await get_loyalty_summary(s, 70026)
    assert summary.is_member is False
    assert summary.available == 0
    assert summary.reserved_until is None


async def test_me_survives_a_reservation_larger_than_the_balance(client, maker, monkeypatch) -> None:
    """A reservation exceeding the balance clamps `available` to 0 — it must not 500 the card.

    `available` is not a bare subtraction for exactly this reason: the response carries the whole
    profile, so one anomalous number must degrade alone, not take the card down with it.
    """
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", "test-bot-token-123")
    user_id = await _seed_profile(maker, 70027, lifetime=1, balance=1_000)
    await _seed_reserve(
        maker, user_id=user_id, points_applied=5_000, age=timedelta(minutes=1), tag="ANOMALY"
    )
    headers = {
        "X-Init-Data": _make_init_data("test-bot-token-123", {"id": 70027, "first_name": "Odd"})
    }
    resp = await client.get("/api/v1/loyalty/me", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["balance"] == 1_000
    assert body["available"] == 0


async def test_reserve_points_still_withholds_an_expired_reservation(maker) -> None:
    """The MONEY path counts reservations of ANY age — only the read-only summary ages them out.

    Not symmetry for its own sake: a reservation often outlives the timeout precisely because its
    invoice was already paid and `delete_invoice` refused to cancel it. Ignoring it here would admit
    a redemption against points that are already committed, and `commit_redeem` would then skip the
    debit rather than drive the balance negative — the goods would leave for points that never existed.
    """
    user_id = await _seed_profile(maker, 70028, lifetime=1, balance=20_000)
    await _seed_reserve(
        maker,
        user_id=user_id,
        points_applied=17_300,
        age=REDEEM_RESERVE_TIMEOUT + timedelta(hours=3),
        tag="STALEPAID",
    )
    new_order_id, _ = await _seed_reserve(
        maker, user_id=user_id, points_applied=0, age=timedelta(seconds=0), tag="NEW"
    )
    async with maker() as s, s.begin():
        with pytest.raises(InsufficientPointsError):
            await reserve_points(
                s, user_id=user_id, order_id=new_order_id, amount=5_000, settle_now=False
            )
