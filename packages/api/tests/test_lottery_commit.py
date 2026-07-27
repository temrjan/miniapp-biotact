"""Freezing a draw (Stage 5, PR-5.3): hash contract, races, irreversibility.

``commit_draw`` owns one explicit transaction, so seeding and the call cannot share the single
``test_db`` session — a second sessionmaker on the same database is used, mirroring
``test_loyalty_survey.py``.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
import pytest_asyncio
from jose import jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.common.exceptions import BadRequestException, ConflictException
from src.config import settings
from src.loyalty.lottery import (
    canonical_snapshot,
    commit_draw,
    committable_periods,
    period_of,
    snapshot_hash,
)
from src.loyalty.models import (
    CustomerProfile,
    Gender,
    LotteryDraw,
    LotteryDrawStatus,
    LotteryTicket,
    LoyaltyStatus,
)
from src.users.models import User

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"
AS_OF = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
PERIOD = "2026-07"


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> object:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _seed_member(
    sm: async_sessionmaker[AsyncSession],
    telegram_id: int,
    *,
    status: LoyaltyStatus = LoyaltyStatus.NEWBIE,
    age: int | None = 30,
    is_adult: bool = False,
    activated_ago: timedelta | None = timedelta(days=2),
) -> int:
    """Commit an eligible member (defaults); returns user id."""
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Member")
        s.add(user)
        await s.flush()
        s.add(
            CustomerProfile(
                user_id=user.id,
                gender=Gender.MALE,
                age=age,
                city="Ташкент",
                is_adult=is_adult,
                status=status,
                last_activation_at=None if activated_ago is None else AS_OF - activated_ago,
            )
        )
        return user.id


# --------------------------------------------------------------------------- #
# Hash contract — byte-exact, reproducible by an outsider
# --------------------------------------------------------------------------- #


def test_canonical_snapshot_is_byte_exact() -> None:
    """The serialization is the published contract: separators, order, no trailing newline."""
    rows = [(2, 77, "friend"), (1, 77, "survey"), (3, 91, "survey")]
    assert canonical_snapshot(rows) == "1;77;survey\n2;77;friend\n3;91;survey"


def test_hash_is_reproducible_by_an_independent_recount() -> None:
    """An outsider recomputing SHA-256 over the published list must get the published value."""
    rows = [(1, 5, "survey"), (2, 5, "friend")]
    independently = sha256(b"1;5;survey\n2;5;friend").hexdigest()
    assert snapshot_hash(rows) == independently


def test_snapshot_order_does_not_depend_on_input_order() -> None:
    """Rows arriving in any order hash the same — the contract sorts by ticket number."""
    rows = [(1, 5, "survey"), (2, 5, "friend"), (3, 9, "survey")]
    assert snapshot_hash(list(reversed(rows))) == snapshot_hash(rows)


@pytest.mark.parametrize(
    "tampered",
    [
        pytest.param([(1, 5, "survey"), (2, 6, "friend")], id="user_swapped"),
        pytest.param([(1, 5, "survey"), (3, 5, "friend")], id="number_changed"),
        pytest.param([(1, 5, "survey"), (2, 5, "survey")], id="source_changed"),
    ],
)
def test_any_ticket_change_changes_the_hash(tampered: list[tuple[int, int, str]]) -> None:
    """Tampering with a single field after publication must be detectable."""
    original = [(1, 5, "survey"), (2, 5, "friend")]
    assert snapshot_hash(tampered) != snapshot_hash(original)


def test_period_of_uses_tashkent_not_utc() -> None:
    """22:00 UTC on 31 July is already 1 August in Tashkent — the label follows the audience."""
    assert period_of(datetime(2026, 7, 31, 22, 0, tzinfo=UTC)) == "2026-08"
    assert period_of(datetime(2026, 7, 31, 18, 0, tzinfo=UTC)) == "2026-07"


# --------------------------------------------------------------------------- #
# Freezing: snapshot content, refusal on empty, irreversibility
# --------------------------------------------------------------------------- #


async def test_commit_writes_snapshot_with_sequential_numbers(test_db: AsyncSession, maker) -> None:
    """Two members (one a friend) → three tickets numbered 1..3 without gaps, hash published."""
    await _seed_member(maker, 90001)
    await _seed_member(maker, 90002, status=LoyaltyStatus.BIOTACT_FRIEND)
    async with maker() as s:
        outcome = await commit_draw(s, period=PERIOD, as_of=AS_OF)

    assert outcome.total_tickets == 3
    async with maker() as s:
        draw = (await s.execute(select(LotteryDraw))).scalar_one()
        assert draw.status == LotteryDrawStatus.COMMITTED.value
        assert draw.participant_hash == outcome.participant_hash
        assert draw.committed_at is not None
        assert draw.hash_published_at is not None
        numbers = (
            (
                await s.execute(
                    select(LotteryTicket.ticket_number).order_by(LotteryTicket.ticket_number)
                )
            )
            .scalars()
            .all()
        )
        assert list(numbers) == [1, 2, 3]


async def test_commit_hash_matches_the_frozen_rows(test_db: AsyncSession, maker) -> None:
    """The published hash is recomputable from the stored tickets — the whole point of commit."""
    await _seed_member(maker, 90003, status=LoyaltyStatus.BIOTACT_FRIEND)
    async with maker() as s:
        outcome = await commit_draw(s, period=PERIOD, as_of=AS_OF)

    async with maker() as s:
        stored = (
            await s.execute(
                select(
                    LotteryTicket.ticket_number, LotteryTicket.user_id, LotteryTicket.source
                ).order_by(LotteryTicket.ticket_number)
            )
        ).all()
    assert snapshot_hash([(n, u, src) for n, u, src in stored]) == outcome.participant_hash


async def test_commit_refuses_when_nobody_qualifies(test_db: AsyncSession, maker) -> None:
    """An empty month is refused, not frozen: freezing it would burn the period irreversibly."""
    await _seed_member(maker, 90004, activated_ago=timedelta(days=90))  # stale activation
    async with maker() as s:
        with pytest.raises(ConflictException):
            await commit_draw(s, period=PERIOD, as_of=AS_OF)

    async with maker() as s:
        assert (await s.execute(select(func.count()).select_from(LotteryDraw))).scalar_one() == 0


async def test_repeat_commit_is_refused_and_changes_nothing(test_db: AsyncSession, maker) -> None:
    """A second freeze of the same period → 409, snapshot untouched (no silent re-snapshot)."""
    await _seed_member(maker, 90005)
    async with maker() as s:
        first = await commit_draw(s, period=PERIOD, as_of=AS_OF)
    await _seed_member(maker, 90006)  # a new member appears AFTER the freeze

    async with maker() as s:
        with pytest.raises(ConflictException):
            await commit_draw(s, period=PERIOD, as_of=AS_OF)

    async with maker() as s:
        draw = (await s.execute(select(LotteryDraw))).scalar_one()
        assert (draw.total_tickets, draw.participant_hash) == (
            first.total_tickets,
            first.participant_hash,
        )


async def test_snapshot_survives_profile_changes(test_db: AsyncSession, maker) -> None:
    """Freezing is a property of the SNAPSHOT: later profile edits cannot move a committed draw."""
    user_id = await _seed_member(maker, 90007, status=LoyaltyStatus.BIOTACT_FRIEND)
    async with maker() as s:
        outcome = await commit_draw(s, period=PERIOD, as_of=AS_OF)

    async with maker() as s, s.begin():  # untick 18+, lose friend, wipe the activation
        profile = (
            await s.execute(select(CustomerProfile).where(CustomerProfile.user_id == user_id))
        ).scalar_one()
        profile.is_adult = False
        profile.age = 15
        profile.status = LoyaltyStatus.NEWBIE
        profile.last_activation_at = None

    async with maker() as s:
        draw = (await s.execute(select(LotteryDraw))).scalar_one()
        tickets = (await s.execute(select(func.count()).select_from(LotteryTicket))).scalar_one()
        assert (draw.total_tickets, draw.participant_hash) == (
            outcome.total_tickets,
            outcome.participant_hash,
        )
        assert tickets == outcome.total_tickets


# --------------------------------------------------------------------------- #
# Races — BOTH cases: the first commit of a period and a repeat one
# --------------------------------------------------------------------------- #


async def test_race_two_first_commits_produce_one_snapshot(test_db: AsyncSession, maker) -> None:
    """Two concurrent FIRST commits → exactly one draw, the loser gets 409 (not a 500, not two).

    This is the case ``FOR UPDATE`` cannot serialize: with no row to lock, both callers pass the
    status check. ``UNIQUE(lottery_draws.period)`` is what decides — removing it (or the flush that
    triggers it) yields two snapshots for one period.
    """
    await _seed_member(maker, 90008)
    async with maker() as s1, maker() as s2:
        results = await asyncio.gather(
            commit_draw(s1, period=PERIOD, as_of=AS_OF),
            commit_draw(s2, period=PERIOD, as_of=AS_OF),
            return_exceptions=True,
        )

    conflicts = [r for r in results if isinstance(r, ConflictException)]
    assert len(conflicts) == 1, results
    assert "уже заморожен" in conflicts[0].detail  # domain message, not the generic global one

    async with maker() as s:
        assert (await s.execute(select(func.count()).select_from(LotteryDraw))).scalar_one() == 1


async def test_race_repeat_commit_is_serialized_by_the_row_lock(
    test_db: AsyncSession, maker
) -> None:
    """Row exists → two concurrent commits serialize on ``SELECT … FOR UPDATE``; one 409, one no-op."""
    await _seed_member(maker, 90009)
    async with maker() as s:
        await commit_draw(s, period=PERIOD, as_of=AS_OF)

    async with maker() as s1, maker() as s2:
        results = await asyncio.gather(
            commit_draw(s1, period=PERIOD, as_of=AS_OF),
            commit_draw(s2, period=PERIOD, as_of=AS_OF),
            return_exceptions=True,
        )
    assert all(isinstance(r, ConflictException) for r in results), results

    async with maker() as s:
        assert (await s.execute(select(func.count()).select_from(LotteryDraw))).scalar_one() == 1


# --------------------------------------------------------------------------- #
# HTTP surface — dark gate, authorization boundary, happy path
# --------------------------------------------------------------------------- #


def _admin_token(user_id: int) -> str:
    """Admin token in the shape `/admin/auth/login` issues (`create_admin_token`)."""
    payload = {
        "sub": str(user_id),
        "telegram_id": 1,
        "type": "admin",
        "exp": int((datetime.now(UTC) + timedelta(minutes=30)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)


def _user_token(user_id: int) -> str:
    """A non-admin token signed with the SAME key — isolates the role check from the signature."""
    payload = {
        "sub": str(user_id),
        "type": "access",
        "exp": int((datetime.now(UTC) + timedelta(minutes=30)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)


async def _seed_admin(sm: async_sessionmaker[AsyncSession], telegram_id: int) -> int:
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Admin", is_admin=True)
        s.add(user)
        await s.flush()
        return user.id


async def test_endpoint_dark_returns_404(client) -> None:
    """loyalty_enabled off → 404 before auth: the surface is not probeable while dark."""
    resp = await client.post(
        "/api/v1/admin/lottery/commit", json={"period": period_of(datetime.now(UTC))}
    )
    assert resp.status_code == 404


async def test_endpoint_requires_a_token(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    resp = await client.post(
        "/api/v1/admin/lottery/commit", json={"period": period_of(datetime.now(UTC))}
    )
    assert resp.status_code == 401  # no credentials at all — refused before the role is looked up


async def test_endpoint_refuses_a_non_admin_token(client, maker, monkeypatch) -> None:
    """A valid NON-admin token must not freeze a draw — the role is checked in the database."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    user_id = await _seed_member(maker, 90020)
    headers = {"Authorization": f"Bearer {_user_token(user_id)}"}
    resp = await client.post(
        "/api/v1/admin/lottery/commit",
        json={"period": period_of(datetime.now(UTC))},
        headers=headers,
    )
    assert resp.status_code == 403, resp.text


async def test_endpoint_refuses_admin_typed_token_of_a_plain_user(
    client, maker, monkeypatch
) -> None:
    """Admin-typed token whose subject is NOT an admin in the DB → refused (this is why we hit it)."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    user_id = await _seed_member(maker, 90021)
    headers = {"Authorization": f"Bearer {_admin_token(user_id)}"}
    resp = await client.post(
        "/api/v1/admin/lottery/commit",
        json={"period": period_of(datetime.now(UTC))},
        headers=headers,
    )
    assert resp.status_code == 403, resp.text


async def test_endpoint_admin_freezes_the_draw(client, maker, monkeypatch) -> None:
    """POSITIVE CONTROL: a real admin freezes the period and gets the published numbers back."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 90022)
    await _seed_member(maker, 90023, status=LoyaltyStatus.BIOTACT_FRIEND)
    headers = {"Authorization": f"Bearer {_admin_token(admin_id)}"}

    resp = await client.post(
        "/api/v1/admin/lottery/commit",
        json={"period": period_of(datetime.now(UTC))},
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_tickets"] == 2
    assert len(body["participant_hash"]) == 64


async def test_endpoint_rejects_a_malformed_period(client, maker, monkeypatch) -> None:
    """`2026-13` is not a month — refused at the edge, before anything is written."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 90024)
    headers = {"Authorization": f"Bearer {_admin_token(admin_id)}"}
    resp = await client.post(
        "/api/v1/admin/lottery/commit", json={"period": "2026-13"}, headers=headers
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# Period window — freezing is irreversible, so the target month is checked
# --------------------------------------------------------------------------- #


def test_committable_window_is_previous_and_current_tashkent_month() -> None:
    """The window is two months wide: the freeze runs 24h before the draw, either side of midnight."""
    assert committable_periods(AS_OF) == ("2026-07", "2026-08")


def test_committable_window_uses_tashkent_not_utc() -> None:
    """22:00 UTC on 31 July is already 1 August in Tashkent — the window moves with the audience."""
    assert committable_periods(datetime(2026, 7, 31, 22, 0, tzinfo=UTC)) == ("2026-07", "2026-08")
    assert committable_periods(datetime(2026, 7, 31, 18, 0, tzinfo=UTC)) == ("2026-06", "2026-07")


@pytest.mark.parametrize(
    "period",
    [
        pytest.param("2027-07", id="future_year_typo"),
        pytest.param("2026-09", id="future_month"),
        pytest.param("2026-05", id="long_past_month"),
    ],
)
async def test_commit_refuses_a_period_outside_the_window(
    test_db: AsyncSession, maker, period: str
) -> None:
    """A typo in the year would burn a period that has not happened yet — refused, nothing written.

    Freezing a long-past month is refused for the mirror reason: it would fill that month's draw
    with TODAY's eligibility, not the eligibility of the month being drawn.
    """
    await _seed_member(maker, 90030)
    async with maker() as s:
        with pytest.raises(BadRequestException):
            await commit_draw(s, period=period, as_of=AS_OF)

    async with maker() as s:
        assert (await s.execute(select(func.count()).select_from(LotteryDraw))).scalar_one() == 0


async def test_commit_accepts_the_current_month_too(test_db: AsyncSession, maker) -> None:
    """The current month is inside the window: the snapshot may be taken on the 1st itself."""
    await _seed_member(maker, 90031)
    async with maker() as s:
        outcome = await commit_draw(s, period="2026-08", as_of=AS_OF)
    assert outcome.period == "2026-08"


async def test_repeat_commit_says_frozen_even_when_participants_went_stale(
    test_db: AsyncSession, maker
) -> None:
    """A repeat freeze reports "already frozen" — not "nobody qualifies" — after eligibility lapses.

    This is what the existence check earns its place with: the branches below it would otherwise
    answer first and hand the administrator a wrong diagnosis about an irreversible operation.
    Removing that check turns this test red.
    """
    user_id = await _seed_member(maker, 90032)
    async with maker() as s:
        await commit_draw(s, period=PERIOD, as_of=AS_OF)

    async with maker() as s, s.begin():  # everyone goes stale after the freeze
        profile = (
            await s.execute(select(CustomerProfile).where(CustomerProfile.user_id == user_id))
        ).scalar_one()
        profile.last_activation_at = None

    async with maker() as s:
        with pytest.raises(ConflictException) as refusal:
            await commit_draw(s, period=PERIOD, as_of=AS_OF)
    assert "уже заморожен" in refusal.value.detail
    assert "нет подходящих участников" not in refusal.value.detail
