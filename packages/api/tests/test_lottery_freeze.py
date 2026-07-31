"""The freeze (Stage 5, step 3): stamping clean tickets into a draw.

What this file is really guarding is the sentence "everyone in the draw qualified at the moment it
was frozen". Everything else — numbering, the confirmation, the refusals — exists to keep that
sentence true and checkable from outside.
"""

import asyncio
from datetime import UTC, date, datetime
from hashlib import sha256

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.common.exceptions import ConflictException
from src.config import settings
from src.loyalty import lottery
from src.loyalty.lottery import commit_draw, export_draw
from src.loyalty.models import (
    CustomerProfile,
    Gender,
    LotteryDraw,
    LotteryDrawStatus,
    LotteryTicket,
    LotteryTicketSource,
)
from src.loyalty.tickets import STATE_SOURCES, sync_tickets
from src.users.models import User

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"

SURVEY = LotteryTicketSource.SURVEY.value
FRIEND = LotteryTicketSource.FRIEND.value
ACTIVATION = LotteryTicketSource.ACTIVATION.value


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> object:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _member_with_tickets(
    sm: async_sessionmaker[AsyncSession],
    telegram_id: int,
    *,
    sources: tuple[str, ...] = (SURVEY,),
    gender: Gender | None = Gender.MALE,
    city: str | None = "Ташкент",
    age: int | None = 30,
    is_adult: bool = False,
) -> int:
    """A club member holding the given clean tickets. Returns their user id."""
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Member")
        s.add(user)
        await s.flush()
        s.add(
            CustomerProfile(
                user_id=user.id, gender=gender, city=city, age=age, is_adult=is_adult
            )
        )
        for source in sources:
            s.add(
                LotteryTicket(
                    user_id=user.id, draw_id=None, ticket_number=None, source=source
                )
            )
        return user.id


async def _stamps(sm: async_sessionmaker[AsyncSession], user_id: int) -> list[int | None]:
    async with sm() as s:
        return list(
            (
                await s.execute(
                    select(LotteryTicket.ticket_number)
                    .where(LotteryTicket.user_id == user_id)
                    .order_by(LotteryTicket.id)
                )
            )
            .scalars()
            .all()
        )


async def _draw_count(sm: async_sessionmaker[AsyncSession]) -> int:
    async with sm() as s:
        return int(
            (await s.execute(select(func.count()).select_from(LotteryDraw))).scalar_one()
        )


# --------------------------------------------------------------------------- #
# T1 — the stamp itself
# --------------------------------------------------------------------------- #


async def test_the_freeze_stamps_clean_tickets_with_contiguous_numbers(
    test_db: AsyncSession, maker
) -> None:
    """Three members, four clean tickets → numbers 1..4 in the order the tickets were issued."""
    first = await _member_with_tickets(maker, 8_400_001, sources=(SURVEY, FRIEND))
    second = await _member_with_tickets(maker, 8_400_002)
    third = await _member_with_tickets(maker, 8_400_003)

    async with maker() as s:
        outcome = await commit_draw(s, expected_tickets=4, prize_label="Набор BIOTACT")

    assert outcome.draw_number == 1
    assert outcome.total_tickets == 4
    assert await _stamps(maker, first) == [1, 2]
    assert await _stamps(maker, second) == [3]
    assert await _stamps(maker, third) == [4]

    async with maker() as s:
        draw = (await s.execute(select(LotteryDraw))).scalar_one()
    assert draw.status == LotteryDrawStatus.COMMITTED.value
    assert draw.period is None  # the calendar binding is gone, not renamed
    assert draw.participant_hash == outcome.participant_hash


async def test_the_announced_date_is_optional_and_stored_as_given(
    test_db: AsyncSession, maker
) -> None:
    """A draw can be frozen without a date — the system's own fact is the press of the button."""
    await _member_with_tickets(maker, 8_401_001)
    async with maker() as s:
        outcome = await commit_draw(s, expected_tickets=1, conducted_on=date(2026, 9, 1))
    assert outcome.conducted_on == date(2026, 9, 1)

    async with maker() as s:
        stored = (await s.execute(select(LotteryDraw.conducted_on))).scalar_one()
    assert stored == date(2026, 9, 1)


# --------------------------------------------------------------------------- #
# T2/T3 — eligibility is re-checked, by the same functions issuance uses
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("kwargs", "case_id"),
    [
        ({"city": None}, "city_cleared_after_the_ticket_was_earned"),
        ({"gender": None}, "gender_cleared"),
        ({"age": 15, "is_adult": True}, "stored_age_says_minor_despite_the_checkbox"),
        ({"age": None, "is_adult": False}, "no_age_and_the_box_is_not_ticked"),
    ],
)
async def test_a_ticket_of_someone_who_stopped_qualifying_stays_clean(
    test_db: AsyncSession, maker, kwargs: dict[str, object], case_id: str
) -> None:
    """The ticket is theirs and stays theirs — it simply does not play.

    One eligible member is seeded beside them, so the freeze has something to do and the assertion
    is about WHO was taken, not about the freeze refusing outright.
    """
    lawful = await _member_with_tickets(maker, 8_402_001)
    unfit = await _member_with_tickets(maker, 8_402_002, **kwargs)  # type: ignore[arg-type]

    async with maker() as s:
        outcome = await commit_draw(s, expected_tickets=1)

    assert outcome.total_tickets == 1
    assert await _stamps(maker, lawful) == [1]
    assert await _stamps(maker, unfit) == [None], f"{case_id}: ticket must remain clean"


async def test_the_freeze_asks_the_same_function_issuance_asks(
    test_db: AsyncSession, maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Break ``has_survey`` and the freeze must find nobody.

    The mechanical proof that eligibility is not restated in SQL somewhere inside the stamp: if it
    were, this monkeypatch would leave the freeze happily stamping tickets and only the issuance
    path would notice. It is the cheapest available guard against the two formulations drifting.
    """
    await _member_with_tickets(maker, 8_403_001)
    monkeypatch.setattr(lottery, "has_survey", lambda _profile: False)

    async with maker() as s:
        with pytest.raises(ConflictException):
            await commit_draw(s, expected_tickets=1)

    assert await _draw_count(maker) == 0


# --------------------------------------------------------------------------- #
# T4 — the boundary the Captain drew: a ticket earned a second later is clean
# --------------------------------------------------------------------------- #


async def test_a_ticket_earned_after_the_freeze_is_clean_and_waits(
    test_db: AsyncSession, maker
) -> None:
    """Freezing takes a snapshot; it does not close the shop."""
    member = await _member_with_tickets(maker, 8_404_001)
    async with maker() as s:
        await commit_draw(s, expected_tickets=1)

    async with maker() as s, s.begin():
        s.add(
            LotteryTicket(
                user_id=member, draw_id=None, ticket_number=None, source=ACTIVATION
            )
        )

    assert await _stamps(maker, member) == [1, None]


async def test_issuance_keeps_working_while_a_draw_is_live(
    test_db: AsyncSession, maker
) -> None:
    """Regression on step 2: a live draw must not block a member from earning their next ticket."""
    await _member_with_tickets(maker, 8_405_001)
    async with maker() as s:
        await commit_draw(s, expected_tickets=1)

    newcomer = await _member_with_tickets(maker, 8_405_002, sources=())
    async with maker() as s, s.begin():
        issued = await sync_tickets(s, user_id=newcomer, sources=STATE_SOURCES)

    assert issued.issued == (SURVEY,)
    assert await _stamps(maker, newcomer) == [None]


# --------------------------------------------------------------------------- #
# T5/T8 — refusals that must not burn a draw number
# --------------------------------------------------------------------------- #


async def test_a_confirmation_that_does_not_match_freezes_nothing(
    test_db: AsyncSession, maker
) -> None:
    """The number is the confirmation; a mismatch is refused with the actual count in the message."""
    member = await _member_with_tickets(maker, 8_406_001, sources=(SURVEY, FRIEND))

    async with maker() as s:
        with pytest.raises(ConflictException) as excinfo:
            await commit_draw(s, expected_tickets=5)

    assert "2" in str(excinfo.value.detail)
    assert await _draw_count(maker) == 0
    assert await _stamps(maker, member) == [None, None]


async def test_a_refused_freeze_does_not_burn_the_draw_number(
    test_db: AsyncSession, maker
) -> None:
    """After a mistyped confirmation the next successful freeze is still №1.

    Refusals happen before the row is inserted precisely for this: an administrator correcting a
    typo should not watch the public numbering skip.
    """
    await _member_with_tickets(maker, 8_407_001)

    async with maker() as s:
        with pytest.raises(ConflictException):
            await commit_draw(s, expected_tickets=99)
    async with maker() as s:
        outcome = await commit_draw(s, expected_tickets=1)

    assert outcome.draw_number == 1


async def test_freezing_with_nothing_to_freeze_is_refused(
    test_db: AsyncSession, maker
) -> None:
    """An empty draw is refused rather than recorded — there is nothing to publish about it."""
    await _member_with_tickets(maker, 8_408_001, sources=(), city=None)

    async with maker() as s:
        with pytest.raises(ConflictException) as excinfo:
            await commit_draw(s, expected_tickets=1)

    assert "нечего" in str(excinfo.value.detail)
    assert await _draw_count(maker) == 0


# --------------------------------------------------------------------------- #
# T6/T7/T7b — one live draw at a time
# --------------------------------------------------------------------------- #


async def test_a_second_freeze_while_a_draw_is_live_is_refused(
    test_db: AsyncSession, maker
) -> None:
    """Sequential case: the message names the draw that has to be closed first."""
    await _member_with_tickets(maker, 8_409_001)
    async with maker() as s:
        await commit_draw(s, expected_tickets=1)

    await _member_with_tickets(maker, 8_409_002)
    async with maker() as s:
        with pytest.raises(ConflictException) as excinfo:
            await commit_draw(s, expected_tickets=1)

    assert "№1" in str(excinfo.value.detail)
    assert await _draw_count(maker) == 1


async def test_the_database_itself_refuses_a_second_committed_draw(
    test_db: AsyncSession, maker
) -> None:
    """The guarantee is declarative: two committed rows cannot coexist, whatever the code does.

    Written at the SQL level on purpose. The application check above answers the sequential case;
    this one is what still holds when two freezes interleave inside the window that check cannot
    cover.
    """
    async with maker() as s, s.begin():
        s.add(LotteryDraw(period=None, status=LotteryDrawStatus.COMMITTED.value, total_tickets=0))

    with pytest.raises(IntegrityError):
        async with maker() as s, s.begin():
            s.add(
                LotteryDraw(period=None, status=LotteryDrawStatus.COMMITTED.value, total_tickets=0)
            )


async def test_the_loser_of_a_concurrent_freeze_is_told_which_draw_is_live(
    test_db: AsyncSession, maker
) -> None:
    """Two freezes racing: the loser gets the SAME diagnosis as the sequential case, with the number.

    The interleaving is made deterministic rather than hoped for: the winner holds an open
    transaction with its committed draw inserted, so the loser's own INSERT blocks on the unique
    index until the winner commits, and only then learns it lost. Without the translation of
    ``IntegrityError`` the loser would get a bare 409 with no body — the same outcome, unexplained.
    """
    await _member_with_tickets(maker, 8_410_001)

    winner_session = maker()
    await winner_session.begin()
    winner_session.add(
        LotteryDraw(period=None, status=LotteryDrawStatus.COMMITTED.value, total_tickets=0)
    )
    await winner_session.flush()

    async def loser() -> ConflictException | None:
        async with maker() as s:
            try:
                await commit_draw(s, expected_tickets=1)
            except ConflictException as exc:
                return exc
        return None

    task = asyncio.create_task(loser())
    await asyncio.sleep(0.2)  # let the loser reach its INSERT and block there
    assert not task.done(), "the loser must be waiting on the unique index, not racing past it"

    await winner_session.commit()
    await winner_session.close()

    failure = await asyncio.wait_for(task, timeout=5)
    assert failure is not None
    assert "№1" in str(failure.detail)


# --------------------------------------------------------------------------- #
# T9 — the published fingerprint
# --------------------------------------------------------------------------- #


async def test_the_exported_bytes_reproduce_the_published_hash(
    test_db: AsyncSession, maker
) -> None:
    """The whole proof in one assertion: what we publish hashes to what we announced."""
    await _member_with_tickets(maker, 8_411_001, sources=(SURVEY, FRIEND))
    await _member_with_tickets(maker, 8_411_002)

    async with maker() as s:
        outcome = await commit_draw(s, expected_tickets=3)
    async with maker() as s:
        exported = await export_draw(s, draw_id=outcome.draw_id)

    assert sha256(exported.body.encode("utf-8")).hexdigest() == outcome.participant_hash
    assert exported.draw_number == outcome.draw_number
    assert exported.cancelled is False
    assert [line.split(";")[0] for line in exported.body.split("\n")] == ["1", "2", "3"]


async def test_the_stamp_moment_is_recorded_as_given(test_db: AsyncSession, maker) -> None:
    """``as_of`` is injectable so nothing in the tests depends on the machine clock."""
    frozen_at = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
    await _member_with_tickets(maker, 8_412_001)

    async with maker() as s:
        outcome = await commit_draw(s, expected_tickets=1, as_of=frozen_at)

    assert outcome.committed_at == frozen_at
    async with maker() as s:
        row = (
            await s.execute(select(LotteryDraw.committed_at, LotteryDraw.hash_published_at))
        ).one()
    assert row.committed_at == frozen_at
    assert row.hash_published_at == frozen_at
