"""The two formulations of "eligible" must agree: Python predicates vs the dashboard's SQL.

Debt of step 2 (МИНОР-1 at its Gate-2), paid here BEFORE step 3 touches the predicates — a step that
edits the rules without this net would make the detector lie silently, and a lying detector is worse
than no detector: it keeps reading 0 while members go without their tickets.

``has_survey``/``is_adult`` decide who gets a ticket and who enters a draw; the dashboard counter
``eligible_without_survey_ticket`` re-expresses them in SQL because an aggregate cannot call them row
by row. Nothing binds the two halves except this file.

The matrix is seeded across every dimension the rules read — gender, city, stored age, the 18+
checkbox, and which ticket source (if any) the member already holds. The detector is an aggregate, so
what can be compared is a COUNT, not a set; the matrix is therefore built so that any disagreement
about WHO qualifies also changes HOW MANY do. That is not free, and it is not theoretical: an earlier
version of this file compared counts over three hand-picked profiles, and a mutation that reversed the
age-vs-checkbox precedence swapped which two were counted while leaving the count at 2 — green, and
proving nothing. The cells that pin a single rule are seeded alone for exactly that reason.

A guard keeps the matrix test from passing vacuously: the expected count must be strictly between
zero and the number of seeded profiles, so a matrix where nothing (or everything) qualifies fails
instead of quietly agreeing at zero.
"""

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.analytics.service import get_loyalty_stats
from src.config import settings
from src.loyalty.lottery import has_survey, is_adult
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

# Every dimension the eligibility rules read.
#
# ⚠️ The ages are not decorative and 15/18/30 is NOT enough. A first version of this file used them
# and a mutation that shifted the SQL threshold to 17 stayed GREEN: no seeded age falls between 17
# and 18, so both formulations answered identically for every row. A matrix without the values that
# separate one threshold from its neighbour cannot catch an off-by-one — it only looks like it can.
# 17 catches a threshold lowered by one, 18 (the inclusive bound) catches one raised by one, ``None``
# is the case where the checkbox is the only thing that speaks.
GENDERS = (None, Gender.MALE)
CITIES = (None, "Ташкент")
AGES = (None, 15, 17, 18, 30)
ADULT_FLAGS = (False, True)
HAS_TICKET = (False, True)


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> object:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _seed_matrix(sm: async_sessionmaker[AsyncSession]) -> int:
    """Seed every combination of the dimensions above; return how many profiles were written."""
    async with sm() as s, s.begin():
        count = 0
        for gender in GENDERS:
            for city in CITIES:
                for age in AGES:
                    for adult_flag in ADULT_FLAGS:
                        for has_ticket in HAS_TICKET:
                            user = User(telegram_id=8_100_000 + count, first_name="M")
                            s.add(user)
                            await s.flush()
                            s.add(
                                CustomerProfile(
                                    user_id=user.id,
                                    gender=gender,
                                    city=city,
                                    age=age,
                                    is_adult=adult_flag,
                                )
                            )
                            if has_ticket:
                                s.add(
                                    LotteryTicket(
                                        user_id=user.id,
                                        draw_id=None,
                                        ticket_number=None,
                                        source=SURVEY,
                                    )
                                )
                            count += 1

        # Two eligible members holding a ticket that is NOT the survey one. Both are still owed a
        # survey ticket, and both formulations have to say so. Without them a copy that forgot to
        # filter by source would answer identically on every row above (every seeded ticket there is
        # a survey ticket) and the drift would go unnoticed.
        for offset, source in enumerate(
            (LotteryTicketSource.ACTIVATION.value, LotteryTicketSource.FRIEND.value)
        ):
            user = User(telegram_id=8_150_000 + offset, first_name="M")
            s.add(user)
            await s.flush()
            s.add(
                CustomerProfile(
                    user_id=user.id, gender=Gender.MALE, city="Ташкент", age=30, is_adult=False
                )
            )
            s.add(LotteryTicket(user_id=user.id, draw_id=None, ticket_number=None, source=source))
            count += 1
        return count


async def _expected_by_python(s: AsyncSession) -> int:
    """The same question answered by the functions the draw itself uses."""
    profiles = (await s.execute(select(CustomerProfile))).scalars().all()
    owners = set(
        (await s.execute(select(LotteryTicket.user_id).where(LotteryTicket.source == SURVEY)))
        .scalars()
        .all()
    )
    return sum(1 for p in profiles if has_survey(p) and is_adult(p) and p.user_id not in owners)


async def test_sql_detector_counts_the_same_set_as_the_python_predicates(
    test_db: AsyncSession, maker
) -> None:
    """Full matrix → the dashboard number equals the Python count, member for member."""
    seeded = await _seed_matrix(maker)

    async with maker() as s:
        expected = await _expected_by_python(s)
        stats = await get_loyalty_stats(s, days=30)

    # Guards against a vacuous pass: a matrix where nobody (or everybody) qualifies would make
    # "0 == 0" look like agreement while proving nothing about the rules.
    assert 0 < expected < seeded, f"matrix does not discriminate: {expected} of {seeded}"
    assert stats.eligible_without_survey_ticket == expected


async def _seed_one(
    sm: async_sessionmaker[AsyncSession],
    telegram_id: int,
    *,
    age: int | None,
    is_adult_flag: bool,
) -> None:
    """One member with a complete survey; only the two age inputs vary."""
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="M")
        s.add(user)
        await s.flush()
        s.add(
            CustomerProfile(
                user_id=user.id,
                gender=Gender.MALE,
                city="Ташкент",
                age=age,
                is_adult=is_adult_flag,
            )
        )


async def _both_counts(sm: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    async with sm() as s:
        return await _expected_by_python(s), (
            await get_loyalty_stats(s, days=30)
        ).eligible_without_survey_ticket


async def test_a_minor_who_ticked_the_box_is_counted_by_neither(
    test_db: AsyncSession, maker
) -> None:
    """Stored 15 + ticked 18+ → eligible for nothing, in both formulations.

    This is the cell where the halves are most likely to drift: Python resolves the contradiction
    explicitly (stored age wins, ratified 2026-07-27) and the SQL copy has to encode the same
    precedence in a CASE. Seeded ALONE on purpose — with a lawful member beside them, a copy that
    reversed the precedence would swap WHO is counted while leaving HOW MANY unchanged, and an
    assertion on the count alone would sleep through it (measured: it did).
    """
    await _seed_one(maker, 8_200_001, age=15, is_adult_flag=True)

    python_count, sql_count = await _both_counts(maker)
    assert (python_count, sql_count) == (0, 0)


async def test_the_checkbox_carries_a_member_with_no_stored_age_in_both(
    test_db: AsyncSession, maker
) -> None:
    """No age stored → the checkbox is all there is, and it decides the same way on both sides."""
    await _seed_one(maker, 8_200_002, age=None, is_adult_flag=True)
    await _seed_one(maker, 8_200_003, age=None, is_adult_flag=False)

    python_count, sql_count = await _both_counts(maker)
    assert (python_count, sql_count) == (1, 1)


async def test_a_stamped_ticket_still_counts_as_owned(test_db: AsyncSession, maker) -> None:
    """After a freeze the survey ticket is no longer clean — and its owner is still not owed one.

    Both formulations ask "was this source EVER earned", not "is it unplayed". Getting this wrong
    would make the detector demand a second survey ticket for everyone in the draw, on every
    dashboard open after the first freeze.
    """
    async with maker() as s, s.begin():
        draw = LotteryDraw(
            period="2026-07",
            status=LotteryDrawStatus.COMMITTED.value,
            total_tickets=1,
        )
        s.add(draw)
        user = User(telegram_id=8_300_000, first_name="M")
        s.add(user)
        await s.flush()
        s.add(CustomerProfile(user_id=user.id, gender=Gender.MALE, city="Ташкент", age=30))
        s.add(LotteryTicket(user_id=user.id, draw_id=draw.id, ticket_number=1, source=SURVEY))

    async with maker() as s:
        expected = await _expected_by_python(s)
        stats = await get_loyalty_stats(s, days=30)

    assert expected == 0
    assert stats.eligible_without_survey_ticket == 0
