"""Cancelling a draw (Stage 5, step 3): stamps go back, the snapshot stays as evidence.

Cancelling is the product-level undo of the freeze, and the only operation in this system that
removes a published artifact from play. Everything here is about the two things that must survive
it: the members' tickets, and the ability of an outsider to check what the cancelled draw contained.
"""

import asyncio
from hashlib import sha256

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.common.exceptions import ConflictException, NotFoundException
from src.config import settings
from src.loyalty.lottery import cancel_draw, commit_draw, export_draw, record_winners
from src.loyalty.models import (
    CustomerProfile,
    Gender,
    LotteryDraw,
    LotteryDrawStatus,
    LotteryTicket,
    LotteryTicketSource,
)
from src.users.models import User

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"

SURVEY = LotteryTicketSource.SURVEY.value


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> object:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _member(sm: async_sessionmaker[AsyncSession], telegram_id: int) -> int:
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Member")
        s.add(user)
        await s.flush()
        s.add(
            CustomerProfile(user_id=user.id, gender=Gender.MALE, city="Ташкент", age=30)
        )
        s.add(
            LotteryTicket(user_id=user.id, draw_id=None, ticket_number=None, source=SURVEY)
        )
        return user.id


async def _frozen_draw(sm: async_sessionmaker[AsyncSession], members: int = 2) -> tuple[int, str]:
    """Seed ``members`` eligible members and freeze. Returns ``(draw_id, participant_hash)``."""
    for index in range(members):
        await _member(sm, 8_500_000 + index)
    async with sm() as s:
        outcome = await commit_draw(s, expected_tickets=members)
    return outcome.draw_id, outcome.participant_hash


async def _draw_row(sm: async_sessionmaker[AsyncSession], draw_id: int) -> LotteryDraw:
    async with sm() as s:
        return (
            await s.execute(select(LotteryDraw).where(LotteryDraw.id == draw_id))
        ).scalar_one()


async def _clean_count(sm: async_sessionmaker[AsyncSession]) -> int:
    async with sm() as s:
        return int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(LotteryTicket)
                    .where(LotteryTicket.draw_id.is_(None))
                )
            ).scalar_one()
        )


# --------------------------------------------------------------------------- #
# T10/T11 — the happy path and the verifiable journal
# --------------------------------------------------------------------------- #


async def test_cancelling_gives_every_ticket_back_as_clean(
    test_db: AsyncSession, maker
) -> None:
    """Both halves of "undo": the draw is closed as cancelled and the tickets are free again."""
    draw_id, _ = await _frozen_draw(maker)
    assert await _clean_count(maker) == 0

    async with maker() as s:
        outcome = await cancel_draw(s, draw_id=draw_id)

    assert outcome.released_tickets == 2
    assert await _clean_count(maker) == 2

    draw = await _draw_row(maker, draw_id)
    assert draw.status == LotteryDrawStatus.CANCELLED.value
    assert draw.cancelled_at is not None

    async with maker() as s:
        numbers = (
            (await s.execute(select(LotteryTicket.ticket_number))).scalars().all()
        )
    assert set(numbers) == {None}, "a released ticket keeps no number from the cancelled draw"


async def test_the_journal_rehashes_to_the_published_fingerprint(
    test_db: AsyncSession, maker
) -> None:
    """This is the difference between "a journal exists" and "a journal is verifiable".

    The stored text is the exact bytes the announced SHA-256 was taken over, so anyone holding the
    published hash can check the cancelled draw's contents without trusting us. Storing the
    composition "in some form" would pass a shallower test and prove nothing.
    """
    draw_id, published = await _frozen_draw(maker, members=3)

    async with maker() as s:
        await cancel_draw(s, draw_id=draw_id)

    draw = await _draw_row(maker, draw_id)
    assert draw.cancelled_snapshot is not None
    assert sha256(draw.cancelled_snapshot.encode("utf-8")).hexdigest() == published
    assert len(draw.cancelled_snapshot.split("\n")) == 3


async def test_the_export_of_a_cancelled_draw_serves_the_journal(
    test_db: AsyncSession, maker
) -> None:
    """The evidence has to be readable exactly when someone doubts the cancellation.

    Recomputing from ``lottery_tickets`` would return an empty body — the stamps are gone — and an
    empty list under a published hash looks like tampering rather than like an undo.
    """
    draw_id, published = await _frozen_draw(maker)
    async with maker() as s:
        await cancel_draw(s, draw_id=draw_id)

    async with maker() as s:
        exported = await export_draw(s, draw_id=draw_id)

    assert exported.cancelled is True
    assert sha256(exported.body.encode("utf-8")).hexdigest() == published


# --------------------------------------------------------------------------- #
# T12 — refusals
# --------------------------------------------------------------------------- #


async def test_a_drawn_draw_cannot_be_cancelled(test_db: AsyncSession, maker) -> None:
    """Once the numbers are recorded there is a result in the world; taking it back is not ours."""
    draw_id, _ = await _frozen_draw(maker)
    async with maker() as s:
        await record_winners(s, draw_id=draw_id, winning_numbers=[1])

    async with maker() as s:
        with pytest.raises(ConflictException) as excinfo:
            await cancel_draw(s, draw_id=draw_id)

    assert "drawn" in str(excinfo.value.detail)
    assert await _clean_count(maker) == 0, "nothing was released by the refusal"


async def test_cancelling_twice_changes_nothing_the_second_time(
    test_db: AsyncSession, maker
) -> None:
    """The second call is refused, and — the point of the test — it does not rewrite the journal."""
    draw_id, _ = await _frozen_draw(maker)
    async with maker() as s:
        await cancel_draw(s, draw_id=draw_id)
    first = await _draw_row(maker, draw_id)

    async with maker() as s:
        with pytest.raises(ConflictException):
            await cancel_draw(s, draw_id=draw_id)

    second = await _draw_row(maker, draw_id)
    assert second.cancelled_at == first.cancelled_at
    assert second.cancelled_snapshot == first.cancelled_snapshot


async def test_cancelling_a_draw_that_does_not_exist_is_a_404(
    test_db: AsyncSession, maker
) -> None:
    async with maker() as s:
        with pytest.raises(NotFoundException):
            await cancel_draw(s, draw_id=4242)


# --------------------------------------------------------------------------- #
# T13 — cancel racing the recording of winners
# --------------------------------------------------------------------------- #


async def test_cancel_and_winners_at_the_same_moment_leave_one_consistent_state(
    test_db: AsyncSession, maker
) -> None:
    """Both operations start inside the SAME contended window, and exactly one survives.

    The window is created rather than hoped for: a third session holds the stamped tickets locked,
    so both callers reach their first UPDATE and wait there. Which of them wins is genuinely
    undecided — that is the point — so the assertion is about the state, not the winner: either the
    draw is drawn with its winner recorded and every stamp intact, or it is cancelled with every
    ticket clean and nobody marked a winner. A mixture is the outcome that must be impossible.
    """
    draw_id, _ = await _frozen_draw(maker)

    async def run(coro_factory) -> Exception | None:
        async with maker() as s:
            try:
                await coro_factory(s)
            except Exception as exc:  # the test asserts on the type below
                return exc
        return None

    blocker = maker()
    cancelling: asyncio.Task[Exception | None] | None = None
    recording: asyncio.Task[Exception | None] | None = None
    try:
        await blocker.begin()
        await blocker.execute(
            text("SELECT id FROM lottery_tickets WHERE draw_id = :d FOR UPDATE"), {"d": draw_id}
        )

        cancelling = asyncio.create_task(run(lambda s: cancel_draw(s, draw_id=draw_id)))
        recording = asyncio.create_task(
            run(lambda s: record_winners(s, draw_id=draw_id, winning_numbers=[1]))
        )
        await asyncio.sleep(0.2)
        both_waiting = not cancelling.done() and not recording.done()
    finally:
        # Always release the blocker: it holds row locks, and leaking them turns a failing
        # assertion into a hung suite — the fixture's ``drop_all`` would wait forever.
        await blocker.rollback()
        await blocker.close()

    assert both_waiting, "both calls must be waiting on the same locked rows"

    failures = [
        result
        for result in await asyncio.wait_for(
            asyncio.gather(cancelling, recording), timeout=10
        )
        if result is not None
    ]
    assert len(failures) == 1, f"exactly one call must fail, got {failures}"
    assert isinstance(failures[0], ConflictException)

    draw = await _draw_row(maker, draw_id)
    async with maker() as s:
        tickets = (await s.execute(select(LotteryTicket))).scalars().all()

    if draw.status == LotteryDrawStatus.CANCELLED.value:
        assert all(t.draw_id is None and t.ticket_number is None for t in tickets)
        assert not any(t.is_winner for t in tickets)
        assert draw.cancelled_snapshot is not None
    else:
        assert draw.status == LotteryDrawStatus.DRAWN.value
        assert all(t.draw_id == draw_id and t.ticket_number is not None for t in tickets)
        assert sum(1 for t in tickets if t.is_winner) == 1
        assert draw.cancelled_snapshot is None


# --------------------------------------------------------------------------- #
# T14 — life after a cancellation
# --------------------------------------------------------------------------- #


async def test_after_a_cancellation_the_next_freeze_takes_a_new_number(
    test_db: AsyncSession, maker
) -> None:
    """The cancelled number is burned: it may already have been published.

    And the released tickets take part in the new draw — that is what "the ticket is yours" means
    when a freeze is undone.
    """
    draw_id, _ = await _frozen_draw(maker)
    async with maker() as s:
        await cancel_draw(s, draw_id=draw_id)

    async with maker() as s:
        second = await commit_draw(s, expected_tickets=2)

    assert second.draw_number == 2, "the cancelled draw's number is not reused"
    assert second.total_tickets == 2
    assert second.draw_id != draw_id

    async with maker() as s:
        stamped = (
            (
                await s.execute(
                    select(LotteryTicket.ticket_number).where(
                        LotteryTicket.draw_id == second.draw_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert sorted(stamped) == [1, 2]
