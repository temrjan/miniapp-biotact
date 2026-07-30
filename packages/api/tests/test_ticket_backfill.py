"""One-off backfill (Stage 5, step 2): what it grants, what it marks, and what it cannot undo."""

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.loyalty.backfill import revert_backfill, run_backfill
from src.loyalty.models import (
    CustomerProfile,
    Gender,
    LotteryDraw,
    LotteryDrawStatus,
    LotteryTicket,
    LotteryTicketSource,
    LoyaltyStatus,
)
from src.users.models import User

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"
BATCH = "initial-2026-08"


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> object:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _seed(
    sm: async_sessionmaker[AsyncSession],
    telegram_id: int,
    *,
    status: LoyaltyStatus = LoyaltyStatus.NEWBIE,
    city: str | None = "Ташкент",
) -> int:
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Member")
        s.add(user)
        await s.flush()
        s.add(
            CustomerProfile(
                user_id=user.id, gender=Gender.FEMALE, city=city, age=30, status=status
            )
        )
        return user.id


async def _tickets(sm: async_sessionmaker[AsyncSession]) -> list[tuple[str, str | None]]:
    async with sm() as s:
        return [
            (row.source, row.backfill_batch)
            for row in (
                await s.execute(
                    select(LotteryTicket.source, LotteryTicket.backfill_batch).order_by(
                        LotteryTicket.id
                    )
                )
            ).all()
        ]


async def test_dry_run_reports_without_writing_anything(test_db: AsyncSession, maker) -> None:
    """The preview runs the REAL issuance in a rolled-back savepoint — it cannot drift from apply."""
    await _seed(maker, 980001)
    await _seed(maker, 980002, status=LoyaltyStatus.BIOTACT_FRIEND)

    async with maker() as s, s.begin():
        report = await run_backfill(s, dry_run=True)

    assert report.members_scanned == 2
    assert report.members_granted == 2
    assert report.by_source == {"survey": 2, "friend": 1}
    assert await _tickets(maker) == [], "a dry run that writes is not a dry run"


async def test_apply_grants_only_the_one_time_sources_and_marks_them(
    test_db: AsyncSession, maker
) -> None:
    """No purchase tickets for purchases made before the feature existed (ratified 2026-07-30)."""
    # A NEWBIE on purpose: a friend would take both one-time tickets and hit the ceiling, so a
    # backfill wrongly allowed to grant purchase tickets would have no room to prove it. With one
    # slot free, the mutation shows itself.
    await _seed(maker, 980003)

    async with maker() as s, s.begin():
        report = await run_backfill(s, batch=BATCH, dry_run=False)

    rows = await _tickets(maker)
    assert report.by_source == {"survey": 1}
    assert sorted(source for source, _ in rows) == ["survey"]
    assert {mark for _, mark in rows} == {BATCH}
    assert LotteryTicketSource.ACTIVATION.value not in [source for source, _ in rows]


async def test_apply_is_idempotent_and_skips_the_ineligible(test_db: AsyncSession, maker) -> None:
    """Running it twice is a no-op; an incomplete profile is not helped along."""
    await _seed(maker, 980004)
    await _seed(maker, 980005, city=None)  # survey incomplete → nothing at all

    async with maker() as s, s.begin():
        first = await run_backfill(s, batch=BATCH, dry_run=False)
    async with maker() as s, s.begin():
        second = await run_backfill(s, batch=BATCH, dry_run=False)

    assert first.members_granted == 1
    assert second.members_granted == 0
    assert len(await _tickets(maker)) == 1


async def test_revert_removes_its_own_batch_only(test_db: AsyncSession, maker) -> None:
    """A member's organically earned ticket is not collateral damage of undoing a backfill."""
    await _seed(maker, 980006)
    organic_user = await _seed(maker, 980007)

    async with maker() as s, s.begin():
        await run_backfill(s, batch=BATCH, dry_run=False)
        # …and one ticket that did NOT come from the backfill
        await s.execute(
            LotteryTicket.__table__.delete().where(LotteryTicket.user_id == organic_user)
        )
        s.add(
            LotteryTicket(
                user_id=organic_user, source=LotteryTicketSource.SURVEY.value, backfill_batch=None
            )
        )

    async with maker() as s, s.begin():
        report = await revert_backfill(s, batch=BATCH)

    assert report.deleted == 1
    assert report.kept_stamped == 0
    assert await _tickets(maker) == [("survey", None)]


async def test_revert_keeps_and_reports_tickets_already_in_a_draw(
    test_db: AsyncSession, maker
) -> None:
    """Undoing stops at the published snapshot: removing a stamped row would break its hash."""
    user_id = await _seed(maker, 980008, status=LoyaltyStatus.BIOTACT_FRIEND)

    async with maker() as s, s.begin():
        await run_backfill(s, batch=BATCH, dry_run=False)
        draw = LotteryDraw(period="2026-07", status=LotteryDrawStatus.COMMITTED.value)
        s.add(draw)
        await s.flush()
        # one of the two goes into the draw
        stamped = (
            await s.execute(select(LotteryTicket.id).where(LotteryTicket.user_id == user_id))
        ).scalars().first()
        await s.execute(
            LotteryTicket.__table__.update()
            .where(LotteryTicket.id == stamped)
            .values(draw_id=draw.id, ticket_number=1)
        )

    async with maker() as s, s.begin():
        report = await revert_backfill(s, batch=BATCH)

    assert report.deleted == 1
    assert report.kept_stamped == 1, "the stamped one must be reported, not silently left behind"
    async with maker() as s:
        left = (await s.execute(select(func.count(LotteryTicket.id)))).scalar_one()
    assert left == 1
