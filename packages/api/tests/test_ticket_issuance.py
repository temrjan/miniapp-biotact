"""Ticket issuance (Stage 5, step 2): who gets what, and what money must never pay for it.

The heaviest test in this file is not about tickets at all — it is
``test_server_side_issuance_failure_does_not_cost_the_member_their_cashback``. Everything else can
be re-run; a lost accrual cannot.
"""

import asyncio
import secrets
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.auth.schemas import TelegramUser
from src.config import settings
from src.loyalty.codes import generate_batch
from src.loyalty.models import (
    CustomerProfile,
    Gender,
    LedgerReason,
    LotteryDraw,
    LotteryDrawStatus,
    LotteryTicket,
    LotteryTicketSource,
    LoyaltyStatus,
    PointLedger,
)
from src.loyalty.service import activate_code
from src.loyalty.tickets import ACTIVATION_SOURCES, CLEAN_TICKET_CEILING, sync_tickets
from src.products.models import Product
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


async def _seed_profile(
    sm: async_sessionmaker[AsyncSession],
    telegram_id: int,
    *,
    gender: Gender | None = Gender.MALE,
    city: str | None = "Ташкент",
    age: int | None = 30,
    is_adult: bool = False,
    status: LoyaltyStatus = LoyaltyStatus.NEWBIE,
) -> int:
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Member")
        s.add(user)
        await s.flush()
        s.add(
            CustomerProfile(
                user_id=user.id,
                gender=gender,
                city=city,
                age=age,
                is_adult=is_adult,
                status=status,
            )
        )
        return user.id


async def _sources_of(sm: async_sessionmaker[AsyncSession], user_id: int) -> list[str]:
    async with sm() as s:
        return list(
            (
                await s.execute(
                    select(LotteryTicket.source)
                    .where(LotteryTicket.user_id == user_id)
                    .order_by(LotteryTicket.id)
                )
            )
            .scalars()
            .all()
        )


async def _run_sync(sm: async_sessionmaker[AsyncSession], user_id: int) -> tuple[str, ...]:
    async with sm() as s, s.begin():
        return (await sync_tickets(s, user_id=user_id)).issued


# --------------------------------------------------------------------------- #
# Eligibility matrix — who is owed what
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        pytest.param({}, [SURVEY], id="complete_adult_newbie"),
        pytest.param(
            {"status": LoyaltyStatus.BIOTACT_FRIEND}, [SURVEY, FRIEND], id="friend_gets_both"
        ),
        pytest.param({"city": None}, [], id="no_city_no_survey_no_ticket"),
        pytest.param({"gender": None}, [], id="no_gender_no_ticket"),
        pytest.param({"age": 15}, [], id="stored_age_under_18_beats_everything"),
        pytest.param(
            {"age": 15, "is_adult": True}, [], id="checkbox_cannot_override_a_known_minor"
        ),
        pytest.param({"age": None, "is_adult": False}, [], id="no_age_no_checkbox_no_ticket"),
        pytest.param({"age": None, "is_adult": True}, [SURVEY], id="checkbox_speaks_when_no_age"),
    ],
)
async def test_issuance_matrix(
    test_db: AsyncSession, maker, kwargs: dict[str, object], expected: list[str]
) -> None:
    """Eligibility is decided by the SAME predicates the freeze re-checks — no second rulebook."""
    user_id = await _seed_profile(maker, secrets.randbelow(10**9), **kwargs)
    await _run_sync(maker, user_id)
    assert await _sources_of(maker, user_id) == expected


async def test_issuing_twice_adds_nothing(test_db: AsyncSession, maker) -> None:
    """Idempotent: the function is called on every survey save and every activation."""
    user_id = await _seed_profile(maker, 970001, status=LoyaltyStatus.BIOTACT_FRIEND)
    first = await _run_sync(maker, user_id)
    second = await _run_sync(maker, user_id)

    assert sorted(first) == [FRIEND, SURVEY]
    assert second == ()
    assert len(await _sources_of(maker, user_id)) == 2


async def test_one_time_sources_do_not_come_back_after_being_played(
    test_db: AsyncSession, maker
) -> None:
    """Once earned, always earned — even when the ticket is already stamped into a draw.

    This is the case that kills the tempting shortcut of keying "already issued?" on the survey
    EVENT: after a draw the member holds no clean survey ticket, and an event-based check would
    happily hand out a second one every month.
    """
    user_id = await _seed_profile(maker, 970002)
    await _run_sync(maker, user_id)

    async with maker() as s, s.begin():  # a draw takes the ticket
        draw = LotteryDraw(period="2026-07", status=LotteryDrawStatus.COMMITTED.value)
        s.add(draw)
        await s.flush()
        await s.execute(
            LotteryTicket.__table__.update()
            .where(LotteryTicket.user_id == user_id)
            .values(draw_id=draw.id, ticket_number=1)
        )

    assert await _run_sync(maker, user_id) == ()
    assert await _sources_of(maker, user_id) == [SURVEY]


async def test_ceiling_blocks_the_renewable_source_not_the_one_time_ones(
    test_db: AsyncSession, maker
) -> None:
    """A friend holding survey+friend is at the ceiling: a purchase adds nothing until a draw."""
    user_id = await _seed_profile(maker, 970003, status=LoyaltyStatus.BIOTACT_FRIEND)
    await _run_sync(maker, user_id)  # survey + friend → at the ceiling

    # The purchase path is the only one that COULD add a third, so the ceiling is only tested by
    # asking for it. Calling the default (state) sources here would prove nothing: those two are
    # already held, and the assertion would pass with the ceiling deleted.
    async with maker() as s, s.begin():
        issued = (await sync_tickets(s, user_id=user_id, sources=ACTIVATION_SOURCES)).issued

    assert issued == ()
    held = await _sources_of(maker, user_id)
    assert len(held) == CLEAN_TICKET_CEILING
    assert ACTIVATION not in held


# --------------------------------------------------------------------------- #
# The money boundary
# --------------------------------------------------------------------------- #


async def _seed_product_and_code(sm: async_sessionmaker[AsyncSession]) -> str:
    async with sm() as s, s.begin():
        product = Product(name="BAD", slug=f"bad-{secrets.token_hex(4)}", price=Decimal("10000"))
        s.add(product)
        await s.flush()
        _, qrs = await generate_batch(s, product.id, 1)
        return qrs[0].code


async def _balance(sm: async_sessionmaker[AsyncSession], user_id: int) -> int:
    async with sm() as s:
        return int(
            (
                await s.execute(
                    select(func.coalesce(func.sum(PointLedger.amount), 0)).where(
                        PointLedger.user_id == user_id
                    )
                )
            ).scalar_one()
        )


async def test_activation_issues_a_purchase_ticket(test_db: AsyncSession, maker) -> None:
    """A real activation of an eligible member yields the renewable ticket."""
    user_id = await _seed_profile(maker, 970004)
    await _run_sync(maker, user_id)  # survey ticket first, as it would be in life
    code = await _seed_product_and_code(maker)

    async with maker() as s:
        await activate_code(
            s, telegram_user=TelegramUser(id=970004, first_name="Member"), code=code
        )

    assert sorted(await _sources_of(maker, user_id)) == [ACTIVATION, SURVEY]


async def test_rescanning_your_own_code_grants_no_extra_ticket(
    test_db: AsyncSession, maker
) -> None:
    """A replay is not a purchase — and `_resolve_unclaimed` is not an issuance point."""
    user_id = await _seed_profile(maker, 970005)
    code = await _seed_product_and_code(maker)
    tg = TelegramUser(id=970005, first_name="Member")

    async with maker() as s:
        await activate_code(s, telegram_user=tg, code=code)
    before = await _sources_of(maker, user_id)
    async with maker() as s:
        await activate_code(s, telegram_user=tg, code=code)

    assert await _sources_of(maker, user_id) == before


async def test_server_side_issuance_failure_does_not_cost_the_member_their_cashback(
    test_db: AsyncSession, maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of the SAVEPOINT, proven with a failure PostgreSQL actually sees.

    The injected failure runs a real statement (``SELECT 1/0``) instead of raising from Python. That
    distinction is the test: a Python-side raise leaves the transaction perfectly healthy, so the
    version WITHOUT ``begin_nested`` would pass too, and the test would prove nothing. A server-side
    error puts the transaction in ``aborted``, where only a rollback is accepted — the accrual then
    survives only if the issuance was wrapped in its own savepoint.
    """

    async def _explode(db: AsyncSession, **_: object) -> None:
        await db.execute(text("SELECT 1/0"))

    monkeypatch.setattr("src.loyalty.service.sync_tickets", _explode)

    user_id = await _seed_profile(maker, 970006)
    code = await _seed_product_and_code(maker)

    async with maker() as s:
        outcome = await activate_code(
            s, telegram_user=TelegramUser(id=970006, first_name="Member"), code=code
        )

    assert outcome.points_awarded > 0
    assert await _balance(maker, user_id) == outcome.points_awarded
    assert await _sources_of(maker, user_id) == [], "issuance failed — no ticket is the right answer"

    async with maker() as s:
        reasons = (
            (
                await s.execute(
                    select(PointLedger.reason).where(PointLedger.user_id == user_id)
                )
            )
            .scalars()
            .all()
        )
    assert LedgerReason.CASHBACK.value in reasons
    assert LedgerReason.WELCOME.value in reasons


async def test_two_concurrent_activations_never_exceed_the_ceiling(
    test_db: AsyncSession, maker
) -> None:
    """Real Postgres contention on the profile lock — the serializer issuance relies on.

    Removing ``.with_for_update()`` from ``sync_tickets`` turns this red: both callers would read
    "no purchase ticket held" before either wrote one.
    """
    user_id = await _seed_profile(maker, 970007)
    await _run_sync(maker, user_id)
    code_a = await _seed_product_and_code(maker)
    code_b = await _seed_product_and_code(maker)
    tg = TelegramUser(id=970007, first_name="Member")

    async def _activate(code: str) -> None:
        async with maker() as s:
            await activate_code(s, telegram_user=tg, code=code)

    await asyncio.gather(_activate(code_a), _activate(code_b))

    held = await _sources_of(maker, user_id)
    assert len(held) <= CLEAN_TICKET_CEILING
    assert held.count(ACTIVATION) == 1, "two purchases before a draw must not stack tickets"


async def test_sync_tickets_contends_for_the_profile_row_lock_itself(
    test_db: AsyncSession, maker
) -> None:
    """The lock ``sync_tickets`` takes ITSELF — proven by making it wait for one already held.

    Two concurrent activations cannot prove this: ``activate_code`` locks the profile before it ever
    reaches issuance, so the inner lock is redundant there and deleting it reddens nothing. Racing a
    backfill against an activation does not prove it either — it was tried, and the backfill simply
    finished first every time, so the interleaving the duplicate needs never happened. Timing-based
    races prove things only when they are lost.

    So the property is asserted directly and deterministically: hold the row lock elsewhere, give
    the caller a short ``lock_timeout``, and require that issuance BLOCKS. Without
    ``with_for_update`` it sails past a locked row and the backfill's check-then-insert is
    unserialized — which is exactly the duplicate this lock exists to prevent.
    """
    user_id = await _seed_profile(maker, 970009)

    async with maker() as holder, holder.begin():
        await holder.execute(
            select(CustomerProfile.id)
            .where(CustomerProfile.user_id == user_id)
            .with_for_update()
        )

        async with maker() as blocked, blocked.begin():
            await blocked.execute(text("SET LOCAL lock_timeout = '400ms'"))
            with pytest.raises(DBAPIError):
                await sync_tickets(blocked, user_id=user_id)

    assert await _sources_of(maker, user_id) == [], "nothing may be written while blocked"
