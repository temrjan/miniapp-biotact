"""My tickets (Stage 5, PR-5.5): what a member sees about their own participation.

The load-bearing decision under test is that a PROSPECTIVE count and a FROZEN one are never
conflated. Before the freeze there are no ``lottery_tickets`` rows at all, so the number is
recomputed and may still change; after the freeze it is fixed. Every state therefore asserts the
shape invariants from the spec, not merely the number.

Seeding and freezing cannot share the ``test_db`` session (``commit_draw`` owns an explicit
transaction) - a second sessionmaker is used, as in ``test_lottery_commit.py``.
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.loyalty.lottery import commit_draw, get_my_tickets, period_of
from src.loyalty.models import (
    CustomerProfile,
    Gender,
    LotteryDraw,
    LotteryDrawStatus,
    LotteryTicket,
    LoyaltyStatus,
)
from src.users.models import User
from tests.conftest import _make_init_data

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"
BOT_TOKEN = "test-bot-token-123"
URL = "/api/v1/loyalty/my-tickets"


def _now() -> datetime:
    """Real current instant. Seeds are relative to it so the 30-day window means what it says."""
    return datetime.now(UTC)


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
    gender: Gender | None = Gender.MALE,
    age: int | None = 30,
    city: str | None = "Ташкент",
    is_adult: bool = False,
    activated_ago: timedelta | None = timedelta(days=2),
) -> int:
    """Commit a member (eligible by default); returns user id."""
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Member")
        s.add(user)
        await s.flush()
        s.add(
            CustomerProfile(
                user_id=user.id,
                gender=gender,
                age=age,
                city=city,
                is_adult=is_adult,
                status=status,
                last_activation_at=None if activated_ago is None else _now() - activated_ago,
            )
        )
        return user.id


async def _seed_user_without_profile(sm: async_sessionmaker[AsyncSession], telegram_id: int) -> int:
    """A registered Telegram user who never joined the club - no ``customer_profile`` row."""
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Stranger")
        s.add(user)
        await s.flush()
        return user.id


async def _freeze(sm: async_sessionmaker[AsyncSession], *, prize: str | None = None) -> str:
    """Freeze the current period; returns the period label."""
    period = period_of(_now())
    async with sm() as s:
        await commit_draw(s, period=period, prize_label=prize, as_of=_now())
    return period


async def _mark_drawn(sm: async_sessionmaker[AsyncSession], *, winner_number: int | None) -> None:
    """Finish the draw by hand - ``record_winners`` belongs to PR-5.4b and does not exist yet."""
    async with sm() as s, s.begin():
        await s.execute(
            update(LotteryDraw).values(status=LotteryDrawStatus.DRAWN.value, drawn_at=_now())
        )
        if winner_number is not None:
            await s.execute(
                update(LotteryTicket)
                .where(LotteryTicket.ticket_number == winner_number)
                .values(is_winner=True, prize_rank=1)
            )


def _headers(telegram_id: int, name: str = "Member") -> dict[str, str]:
    return {"X-Init-Data": _make_init_data(BOT_TOKEN, {"id": telegram_id, "first_name": name})}


def _enable(monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    monkeypatch.setattr(settings, "telegram_bot_token", BOT_TOKEN)


def _assert_invariants(body: dict) -> None:
    """Shape invariants I1, I2 and I4 from the spec - asserted rather than described.

    Kept in one helper and called from every state test on purpose: an invariant checked only in
    the state that happens to satisfy it is not an invariant.

    I3 ("result is present exactly when the latest draw has been drawn") is deliberately NOT here
    and its absence is not an oversight: it relates the response to the draw's stored status, which
    this helper cannot see - it only receives the body. Each state test asserts it directly instead,
    by knowing which state it seeded.
    """
    assert (body["period"] is not None) == body["is_frozen"], "I1: period <=> is_frozen"
    if body["prize_label"] is not None:
        assert body["is_frozen"], "I2: a prize is known only from a frozen draw"
    if body["result"] is not None:
        assert body["is_frozen"] is False, "I4: after a finished draw the top block is prospective"


# --------------------------------------------------------------------------- #
# Three states
# --------------------------------------------------------------------------- #


async def test_no_draw_yet_shows_prospective_count(client, maker, monkeypatch) -> None:
    """No draw exists yet, so the count is prospective and nothing is frozen."""
    _enable(monkeypatch)
    await _seed_member(maker, 92001, status=LoyaltyStatus.BIOTACT_FRIEND)

    resp = await client.get(URL, headers=_headers(92001))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tickets"] == 2  # survey (1) + friend (+1)
    assert body["is_frozen"] is False
    assert body["period"] is None
    assert body["prize_label"] is None
    assert body["result"] is None
    _assert_invariants(body)


async def test_committed_draw_shows_frozen_count(client, maker, monkeypatch) -> None:
    """Once frozen, the number comes from the stored rows and participation is fixed."""
    _enable(monkeypatch)
    await _seed_member(maker, 92011, status=LoyaltyStatus.BIOTACT_FRIEND)
    period = await _freeze(maker, prize="Аэрогриль")

    resp = await client.get(URL, headers=_headers(92011))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tickets"] == 2
    assert body["is_frozen"] is True
    assert body["period"] == period
    assert body["prize_label"] == "Аэрогриль"
    assert body["result"] is None
    _assert_invariants(body)


async def test_drawn_carries_both_the_result_and_a_fresh_prospective_count(
    client, maker, monkeypatch
) -> None:
    """A finished draw returns its outcome AND a prospective count for the next period.

    Spec section 1: the third row is not decoration. Showing a bare zero to an active member after
    a finished draw would say they are out, while they are already accumulating for next month.
    """
    _enable(monkeypatch)
    await _seed_member(maker, 92021, status=LoyaltyStatus.BIOTACT_FRIEND)
    period = await _freeze(maker, prize="Аэрогриль")
    await _mark_drawn(maker, winner_number=1)

    resp = await client.get(URL, headers=_headers(92021))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # top block: prospective, about the NEXT draw
    assert body["is_frozen"] is False
    assert body["period"] is None
    assert body["tickets"] == 2, "the prospective count must not vanish after a draw"
    # bottom block: about the finished one
    result = body["result"]
    assert result is not None
    assert result["period"] == period
    assert result["tickets"] == 2
    assert result["won"] is True
    assert result["prize_rank"] == 1
    assert result["prize_label"] == "Аэрогриль"
    _assert_invariants(body)


async def test_someone_who_was_not_in_the_draw_is_not_told_they_lost(
    client, maker, monkeypatch
) -> None:
    """A member who joined AFTER the freeze held no tickets in that draw - so there is no outcome.

    Reporting ``won=False`` here would turn "did not take part" into "lost", which is a different
    statement about a person and the wrong one. Caught by the Reviewer at Gate-2.
    """
    _enable(monkeypatch)
    await _seed_member(maker, 92071)  # was in the snapshot
    await _freeze(maker)
    await _mark_drawn(maker, winner_number=1)
    await _seed_member(maker, 92072)  # joined after the freeze - never in that draw

    resp = await client.get(URL, headers=_headers(92072))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["result"] is None, "no tickets in that draw means no outcome, not a loss"
    assert body["tickets"] == 1, "and they still accumulate towards the next one"
    _assert_invariants(body)


async def test_drawn_without_a_win_reports_a_loss_not_an_absence(
    client, maker, monkeypatch
) -> None:
    """Losing is an OUTCOME, not a missing outcome: result is present with won=False."""
    _enable(monkeypatch)
    await _seed_member(maker, 92031)
    await _freeze(maker)
    await _mark_drawn(maker, winner_number=None)

    resp = await client.get(URL, headers=_headers(92031))

    body = resp.json()
    assert body["result"] is not None
    assert body["result"]["won"] is False
    assert body["result"]["prize_rank"] is None
    _assert_invariants(body)


# --------------------------------------------------------------------------- #
# Self-scope and read-only
# --------------------------------------------------------------------------- #


async def test_each_member_sees_only_their_own_tickets(client, maker, monkeypatch) -> None:
    """Two members in one draw: each sees their own number, not the sum and not the other's."""
    _enable(monkeypatch)
    await _seed_member(maker, 92041, status=LoyaltyStatus.BIOTACT_FRIEND)  # 2 tickets
    await _seed_member(maker, 92042)  # 1 ticket
    await _freeze(maker)

    friend = (await client.get(URL, headers=_headers(92041))).json()
    newbie = (await client.get(URL, headers=_headers(92042))).json()

    assert friend["tickets"] == 2
    assert newbie["tickets"] == 1


async def test_get_creates_no_rows_for_an_unknown_user(client, maker, monkeypatch) -> None:
    """A pure read: opening the card must not cost a single row."""
    _enable(monkeypatch)

    async with maker() as s:
        users_before = (await s.execute(select(func.count()).select_from(User))).scalar_one()
        profiles_before = (
            await s.execute(select(func.count()).select_from(CustomerProfile))
        ).scalar_one()

    resp = await client.get(URL, headers=_headers(92051, "Nobody"))

    assert resp.status_code == 200, resp.text
    assert resp.json()["tickets"] == 0
    async with maker() as s:
        users_after = (await s.execute(select(func.count()).select_from(User))).scalar_one()
        profiles_after = (
            await s.execute(select(func.count()).select_from(CustomerProfile))
        ).scalar_one()
    assert (users_after, profiles_after) == (users_before, profiles_before)


async def test_user_without_a_profile_gets_a_zero_answer(client, maker, monkeypatch) -> None:
    """The user exists but never joined the club (no profile row): zero, not 404 and not 500.

    This is NOT the same as a partially filled survey - the profile is absent entirely. ``/me``
    keeps a named branch for it (``_non_member_summary``); assuming it works here by inheritance
    is exactly what must not be assumed.
    """
    _enable(monkeypatch)
    await _seed_user_without_profile(maker, 92061)

    resp = await client.get(URL, headers=_headers(92061, "Stranger"))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tickets"] == 0
    assert body["result"] is None
    _assert_invariants(body)


# --------------------------------------------------------------------------- #
# Prospective count must equal the shipped ticket formula
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("status", "gender", "age", "city", "is_adult", "activated_ago", "expected"),
    [
        pytest.param(
            LoyaltyStatus.NEWBIE, Gender.MALE, 30, "Ташкент", False, timedelta(days=2), 1,
            id="survey_plus_age_over_18_plus_fresh_activation",
        ),
        pytest.param(
            LoyaltyStatus.BIOTACT_FRIEND, Gender.MALE, 30, "Ташкент", False, timedelta(days=2), 2,
            id="friend_gets_a_second_ticket",
        ),
        pytest.param(
            LoyaltyStatus.NEWBIE, Gender.MALE, None, "Ташкент", True, timedelta(days=2), 1,
            id="no_age_so_the_checkbox_decides",
        ),
        pytest.param(
            LoyaltyStatus.NEWBIE, Gender.MALE, None, "Ташкент", False, timedelta(days=2), 0,
            id="no_age_and_no_checkbox",
        ),
        pytest.param(
            LoyaltyStatus.NEWBIE, Gender.MALE, 15, "Ташкент", True, timedelta(days=2), 0,
            id="age_15_checkbox_does_not_override_data",
        ),
        pytest.param(
            LoyaltyStatus.NEWBIE, Gender.MALE, 30, None, False, timedelta(days=2), 0,
            id="no_city_survey_incomplete",
        ),
        pytest.param(
            LoyaltyStatus.NEWBIE, None, 30, "Ташкент", False, timedelta(days=2), 0,
            id="no_gender_survey_incomplete",
        ),
        pytest.param(
            LoyaltyStatus.NEWBIE, Gender.MALE, 30, "Ташкент", False, timedelta(days=31), 0,
            id="activation_older_than_30_days",
        ),
        pytest.param(
            LoyaltyStatus.NEWBIE, Gender.MALE, 30, "Ташкент", False, None, 0,
            id="never_activated",
        ),
    ],
)
async def test_prospective_count_matches_the_ticket_formula(
    client, maker, monkeypatch, status, gender, age, city, is_adult, activated_ago, expected
) -> None:
    """The prospective count must equal the shipped formula, not live a life of its own."""
    _enable(monkeypatch)
    await _seed_member(
        maker,
        92100,
        status=status,
        gender=gender,
        age=age,
        city=city,
        is_adult=is_adult,
        activated_ago=activated_ago,
    )

    body = (await client.get(URL, headers=_headers(92100))).json()

    assert body["tickets"] == expected
    assert body["is_frozen"] is False


# --------------------------------------------------------------------------- #
# Service level - deterministic as_of
# --------------------------------------------------------------------------- #


async def test_service_honours_an_injected_as_of(test_db: AsyncSession, maker) -> None:
    """``as_of`` is injectable as in ``commit_draw``: the count does not depend on the wall clock."""
    await _seed_member(maker, 92200, activated_ago=timedelta(days=20))

    async with maker() as s:
        fresh = await get_my_tickets(s, telegram_id=92200, as_of=_now())
        stale = await get_my_tickets(s, telegram_id=92200, as_of=_now() + timedelta(days=15))

    assert fresh.tickets == 1
    assert stale.tickets == 0, "35 days after the activation the ticket is no longer earned"


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #


async def test_endpoint_dark_returns_404(client) -> None:
    """loyalty_enabled off: 404 before auth, so the dark surface is not probeable."""
    resp = await client.get(URL)
    assert resp.status_code == 404


async def test_endpoint_requires_init_data(client, monkeypatch) -> None:
    _enable(monkeypatch)
    resp = await client.get(URL)
    assert resp.status_code == 401
