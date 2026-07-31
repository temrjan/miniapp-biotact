"""My tickets (Stage 5): what a member sees about their own participation.

The load-bearing decision under test is that the stake in a LIVE draw and the tickets a member holds
NOW are never conflated. While a draw is frozen the number is what they have in it; otherwise it is
their clean tickets. Every state asserts the shape invariants from the spec, not merely the number.

Since step 3 both numbers are counts of rows the member owns — nothing is recomputed from a profile,
so nothing here depends on the clock any more.

Seeding and freezing cannot share the ``test_db`` session (``commit_draw`` owns an explicit
transaction) - a second sessionmaker is used, as in ``test_lottery_commit.py``.
"""

from datetime import UTC, datetime

import pytest_asyncio
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.loyalty.lottery import cancel_draw, commit_draw, record_winners
from src.loyalty.models import (
    CustomerProfile,
    Gender,
    LotteryDraw,
    LotteryDrawStatus,
    LotteryTicket,
    LotteryTicketSource,
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
    tickets: int = 1,
    gender: Gender | None = Gender.MALE,
    age: int | None = 30,
    city: str | None = "Ташкент",
    is_adult: bool = False,
) -> int:
    """Commit an eligible member holding ``tickets`` clean tickets; returns user id."""
    sources = (
        LotteryTicketSource.SURVEY.value,
        LotteryTicketSource.FRIEND.value,
        LotteryTicketSource.ACTIVATION.value,
    )
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Member")
        s.add(user)
        await s.flush()
        s.add(
            CustomerProfile(
                user_id=user.id, gender=gender, age=age, city=city, is_adult=is_adult
            )
        )
        for source in sources[:tickets]:
            s.add(
                LotteryTicket(
                    user_id=user.id, draw_id=None, ticket_number=None, source=source
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


async def _freeze(
    sm: async_sessionmaker[AsyncSession], *, expected: int, prize: str | None = None
) -> str:
    """Freeze every eligible clean ticket; returns the label the member sees («№1»)."""
    async with sm() as s:
        outcome = await commit_draw(s, expected_tickets=expected, prize_label=prize)
    return f"№{outcome.draw_number}"


async def _mark_drawn(sm: async_sessionmaker[AsyncSession], *, winner_number: int | None) -> None:
    """Finish the draw by hand: these tests are about the CARD, not about recording winners.

    ``record_winners`` exists and is covered in ``test_lottery_winners.py``; going through it here
    would drag its own refusals into every reading of the card.
    """
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


async def _give_clean_ticket(sm: async_sessionmaker[AsyncSession], user_id: int) -> None:
    """A ticket earned after the freeze — clean, waiting for the next draw."""
    async with sm() as s, s.begin():
        s.add(
            LotteryTicket(
                user_id=user_id,
                draw_id=None,
                ticket_number=None,
                source=LotteryTicketSource.ACTIVATION.value,
            )
        )


async def _user_id(sm: async_sessionmaker[AsyncSession], telegram_id: int) -> int:
    async with sm() as s:
        return int(
            (
                await s.execute(select(User.id).where(User.telegram_id == telegram_id))
            ).scalar_one()
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
    await _seed_member(maker, 92001, tickets=2)

    resp = await client.get(URL, headers=_headers(92001))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tickets"] == 2  # two clean tickets, held by the member and not yet in a draw
    assert body["is_frozen"] is False
    assert body["period"] is None
    assert body["prize_label"] is None
    assert body["result"] is None
    _assert_invariants(body)


async def test_committed_draw_shows_frozen_count(client, maker, monkeypatch) -> None:
    """Once frozen, the number comes from the stored rows and participation is fixed."""
    _enable(monkeypatch)
    await _seed_member(maker, 92011, tickets=2)
    label = await _freeze(maker, expected=2, prize="Аэрогриль")

    resp = await client.get(URL, headers=_headers(92011))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tickets"] == 2
    assert body["is_frozen"] is True
    assert body["period"] == label
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
    member = await _seed_member(maker, 92021, tickets=2)
    label = await _freeze(maker, expected=2, prize="Аэрогриль")
    await _mark_drawn(maker, winner_number=1)
    await _give_clean_ticket(maker, member)  # earned after the draw closed

    resp = await client.get(URL, headers=_headers(92021))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # top block: prospective, about the NEXT draw
    assert body["is_frozen"] is False
    assert body["period"] is None
    assert body["tickets"] == 1, "what they hold now: the ticket earned after the draw"
    # bottom block: about the finished one
    result = body["result"]
    assert result is not None
    assert result["period"] == label
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
    await _freeze(maker, expected=1)
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
    await _freeze(maker, expected=1)
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
    await _seed_member(maker, 92041, tickets=2)
    await _seed_member(maker, 92042, tickets=1)
    await _freeze(maker, expected=3)

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
# What the number means now: rows the member owns
# --------------------------------------------------------------------------- #


async def test_the_count_is_the_members_clean_tickets_not_a_recount(
    client, maker, monkeypatch
) -> None:
    """Eligibility is not recomputed for the card: a ticket earned is a ticket shown.

    The member below no longer qualifies — their city is gone — and they still see the ticket they
    earned, because it is theirs. What it does NOT do is play: the freeze leaves it clean (asserted
    in ``test_lottery_freeze.py``). Step 5 adds the sentence explaining that to them.
    """
    _enable(monkeypatch)
    await _seed_member(maker, 92100, tickets=2, city=None)

    body = (await client.get(URL, headers=_headers(92100))).json()

    assert body["tickets"] == 2
    assert body["is_frozen"] is False


async def test_a_live_draw_shows_the_stake_in_it(client, maker, monkeypatch) -> None:
    """While a draw is frozen the number is participation in THAT draw, and it cannot change.

    ⚠️ Known gap until step 5: the ticket earned after the freeze is real, clean and NOT shown here.
    Pinned deliberately — when the card is rewritten this assertion has to be revisited, not
    silently satisfied.
    """
    _enable(monkeypatch)
    member = await _seed_member(maker, 92110, tickets=1)
    await _freeze(maker, expected=1)
    await _give_clean_ticket(maker, member)

    body = (await client.get(URL, headers=_headers(92110))).json()

    assert body["tickets"] == 1
    assert body["is_frozen"] is True


# --------------------------------------------------------------------------- #
# A cancelled draw must not hide the one before it
# --------------------------------------------------------------------------- #


async def test_a_cancelled_draw_is_skipped_in_favour_of_the_last_real_one(
    client, maker, monkeypatch
) -> None:
    """Taking simply the latest draw would erase a member's prize the moment a later one is cancelled.

    Sequence: draw №1 is held and won, draw №2 is frozen and then cancelled. The member must still
    see the result of №1 — and their released ticket back in hand.
    """
    _enable(monkeypatch)
    await _seed_member(maker, 92120, tickets=1)
    first_label = await _freeze(maker, expected=1, prize="Аэрогриль")
    async with maker() as s:
        first = (
            await s.execute(select(LotteryDraw.id).order_by(LotteryDraw.draw_number))
        ).scalar_one()
    async with maker() as s:
        await record_winners(s, draw_id=first, winning_numbers=[1])

    await _give_clean_ticket(maker, (await _user_id(maker, 92120)))
    await _freeze(maker, expected=1)
    async with maker() as s:
        second = (
            await s.execute(
                select(LotteryDraw.id)
                .where(LotteryDraw.status == LotteryDrawStatus.COMMITTED.value)
            )
        ).scalar_one()
    async with maker() as s:
        await cancel_draw(s, draw_id=second)

    body = (await client.get(URL, headers=_headers(92120))).json()

    assert body["is_frozen"] is False, "the cancelled draw is not a live one"
    assert body["result"] is not None, "the win in №1 must not disappear behind a cancelled №2"
    assert body["result"]["period"] == first_label
    assert body["result"]["won"] is True
    assert body["tickets"] == 1, "the released ticket is clean again and back in hand"
    _assert_invariants(body)


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
