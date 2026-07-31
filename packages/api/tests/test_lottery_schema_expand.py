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


async def test_draw_numbers_come_from_the_database_identity(
    test_db: AsyncSession, maker
) -> None:
    """A writer that never mentions ``draw_number`` still gets one, and the numbers increase.

    Written against the TABLE rather than through the freeze on purpose: this file is about the
    schema, and the freeze has its own tests. Two rows, because a single one cannot show where the
    number came from — an identity sequence keeps handing out increasing values, which is what the
    contract phase promotes to NOT NULL and what ``MAX + 1`` could not promise under concurrency.

    The rows are inserted as ``drawn``: at most one draw may be ``committed`` at a time, and that is
    a different guarantee, tested where it belongs.
    """
    async with maker() as s, s.begin():
        s.add_all(
            [
                LotteryDraw(status=LotteryDrawStatus.DRAWN.value, total_tickets=0),
                LotteryDraw(status=LotteryDrawStatus.DRAWN.value, total_tickets=0),
            ]
        )

    async with maker() as s:
        numbers = (
            (await s.execute(select(LotteryDraw.draw_number).order_by(LotteryDraw.id)))
            .scalars()
            .all()
        )

    assert None not in numbers, "identity default did not fire"
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
