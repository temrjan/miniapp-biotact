"""EXPAND phase of the ticket-as-a-record model (Stage 5, step 1): schema, not behaviour.

Both tests here answer the only question a purely additive schema PR can be wrong about:
**does the code that is already in production keep working after this migration lands?**
Neither test knows anything about tickets-as-records — that logic arrives in steps 2-3. They pin
the two properties the later steps stand on, and each one fails against a plausible wrong version
of this migration (see the mutations in the spec).
"""

from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.loyalty.lottery import commit_draw
from src.loyalty.models import (
    CustomerProfile,
    Gender,
    LotteryDraw,
    LotteryTicket,
    LoyaltyStatus,
)
from src.users.models import User

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"
AS_OF = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> object:
    """A second sessionmaker on the test database — ``commit_draw`` owns its own transaction."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _seed_member(sm: async_sessionmaker[AsyncSession], telegram_id: int) -> int:
    """An eligible member (filled survey, adult by age, recent activation); returns user id."""
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Member")
        s.add(user)
        await s.flush()
        s.add(
            CustomerProfile(
                user_id=user.id,
                gender=Gender.MALE,
                age=30,
                city="Ташкент",
                status=LoyaltyStatus.NEWBIE,
                last_activation_at=AS_OF - timedelta(days=2),
            )
        )
        return user.id


async def test_unchanged_commit_draw_still_works_and_gets_a_number_from_the_database(
    test_db: AsyncSession, maker
) -> None:
    """The pre-step-3 freeze knows nothing about ``draw_number`` — the database supplies it.

    This is the compatibility guarantee of the whole EXPAND phase in one assertion. ``commit_draw``
    is untouched by this PR: it inserts a draw naming ``period``, ``status``, ``total_tickets`` and
    the hash, and never mentions ``draw_number``. If the column needed the writer's help, every
    freeze between this step and step 3 would fail — and so would this test.

    Two draws, because a single one cannot show where the number comes from: values handed out by
    an identity sequence keep increasing across rows, which is what the contract phase will later
    promote to NOT NULL and what ``MAX + 1`` under concurrency could not promise.
    """
    await _seed_member(maker, 96001)

    async with maker() as s:
        first = await commit_draw(s, period="2026-07", as_of=AS_OF)
    async with maker() as s:
        second = await commit_draw(s, period="2026-08", as_of=AS_OF)

    async with maker() as s:
        numbers = (
            (
                await s.execute(
                    select(LotteryDraw.draw_number).order_by(LotteryDraw.id)
                )
            )
            .scalars()
            .all()
        )

    assert first.draw_id != second.draw_id
    assert None not in numbers, "identity default did not fire — the pre-step-3 writer is broken"
    assert numbers[1] > numbers[0], "numbers must come from a sequence, not from a constant"


async def test_clean_tickets_do_not_collide_on_the_snapshot_uniqueness(
    test_db: AsyncSession, maker
) -> None:
    """Many unstamped tickets coexist: ``UNIQUE(draw_id, ticket_number)`` sees NULLs as distinct.

    The whole ticket-as-a-record model rests on this: from step 2 on, every member holds up to two
    tickets that belong to no draw and carry no number. Had the existing unique index treated NULLs
    as equal — as a couple of other engines do — the second clean ticket in the database would have
    been rejected, and the model would have had to be redesigned around a new index instead of
    reusing the one that guards the published snapshot.
    """
    user_id = await _seed_member(maker, 96002)

    async with maker() as s, s.begin():
        s.add_all(
            LotteryTicket(user_id=user_id, draw_id=None, ticket_number=None, source=source)
            for source in ("survey", "friend")
        )

    async with maker() as s:
        clean = (
            await s.execute(
                select(func.count())
                .select_from(LotteryTicket)
                .where(LotteryTicket.draw_id.is_(None))
            )
        ).scalar_one()
        marks = (
            (await s.execute(select(LotteryTicket.backfill_batch))).scalars().all()
        )

    assert clean == 2
    assert set(marks) == {None}, "ordinary issuance must leave the backfill marker empty"
