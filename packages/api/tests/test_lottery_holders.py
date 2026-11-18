"""Who holds tickets, and on what grounds (Stage 5, drill-down behind the dashboard numbers).

The endpoint answers a question the three counters cannot: a number says twelve, it does not say
whose or why. Two things here are load-bearing rather than decorative:

* ``filter=owed`` must return exactly the members the dashboard detector counts. Since the decision
  of 2026-08-04 not to backfill tickets, that counter is no longer expected to read zero, so it
  stopped being an alarm and became a list -- and a list that disagrees with its own counter is
  worse than either alone. They are bound structurally (one SQL expression, two call sites) and
  pinned here.

* ``owed`` is ordered by ``last_activation_at`` DESC **NULLS LAST**. A member who activated a QR
  after ticket issuance shipped and still holds no survey ticket is a real failure of issuance —
  activation calls the issuer with every source. That signal lives in the TOP row, and PostgreSQL
  sorts NULLs FIRST on DESC by default: without ``NULLS LAST`` the never-activated (entry without QR
  exists since Stage 4) bury it. The fixture therefore seeds a NULL profile on purpose; a matrix of
  merely-old dates stays green against the broken ordering and proves nothing.
"""

from datetime import UTC, date, datetime, timedelta

import jwt
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.loyalty.lottery import commit_draw, record_winners
from src.loyalty.models import (
    CustomerProfile,
    Gender,
    LotteryTicket,
    LotteryTicketSource,
    LoyaltyStatus,
)
from src.users.models import User

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"

HOLDERS_URL = "/api/v1/admin/lottery/holders"


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> object:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


def _admin_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "telegram_id": 1,
        "type": "admin",
        "exp": int((datetime.now(UTC) + timedelta(minutes=30)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)


def _user_token(user_id: int) -> str:
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


async def _seed_plain_user(sm: async_sessionmaker[AsyncSession], telegram_id: int) -> int:
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Plain")
        s.add(user)
        await s.flush()
        return user.id


async def _seed_member(
    sm: async_sessionmaker[AsyncSession],
    telegram_id: int,
    *,
    name: str = "Member",
    phone: str | None = "+998901112233",
    gender: Gender | None = Gender.MALE,
    age: int | None = 30,
    city: str | None = "Ташкент",
    is_adult: bool = False,
    status: LoyaltyStatus = LoyaltyStatus.NEWBIE,
    last_activation_at: datetime | None = None,
    sources: tuple[LotteryTicketSource, ...] = (LotteryTicketSource.SURVEY,),
) -> int:
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name=name, phone=phone)
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
                last_activation_at=last_activation_at,
            )
        )
        for source in sources:
            s.add(
                LotteryTicket(
                    user_id=user.id,
                    draw_id=None,
                    ticket_number=None,
                    source=source.value,
                )
            )
        return user.id


async def _get(client, token: str, **params: object) -> object:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{HOLDERS_URL}?{query}" if query else HOLDERS_URL
    return await client.get(url, headers={"Authorization": f"Bearer {token}"})


# --------------------------------------------------------------------------- #
# T7 — boundary: the same five gates every admin endpoint in this router answers
# --------------------------------------------------------------------------- #


async def test_dark_returns_404(client) -> None:
    resp = await client.get(HOLDERS_URL)
    assert resp.status_code == 404


async def test_requires_a_token(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    resp = await client.get(HOLDERS_URL)
    assert resp.status_code == 401


async def test_refuses_a_plain_user_token(client, maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    user_id = await _seed_plain_user(maker, 5001)
    resp = await _get(client, _user_token(user_id))
    assert resp.status_code == 403


async def test_refuses_an_admin_typed_token_of_a_non_admin(client, maker, monkeypatch) -> None:
    """The token says type=admin; the database says the user is not one. The database decides."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    user_id = await _seed_plain_user(maker, 5002)
    resp = await _get(client, _admin_token(user_id))
    assert resp.status_code == 403


async def test_admin_gets_through(client, maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 5003)
    resp = await _get(client, _admin_token(admin_id))
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# T1 — «за что»: the source breakdown is the whole point of the screen
# --------------------------------------------------------------------------- #


async def test_row_breaks_tickets_down_by_source(client, maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 5010)
    await _seed_member(
        maker,
        5011,
        name="Трёхбилетный",
        sources=(
            LotteryTicketSource.SURVEY,
            LotteryTicketSource.FRIEND,
            LotteryTicketSource.ACTIVATION,
        ),
    )

    resp = await _get(client, _admin_token(admin_id), filter="all")
    assert resp.status_code == 200
    rows = resp.json()["items"]
    row = next(r for r in rows if r["name"] == "Трёхбилетный")
    assert row["tickets_total"] == 3
    assert row["tickets_clean"] == 3
    assert row["by_source"] == {"survey": 1, "friend": 1, "activation": 1}


async def test_row_carries_the_phone(client, maker, monkeypatch) -> None:
    """Ratified 2026-08-04: names repeat in Telegram, the phone is how a member is identified."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 5012)
    await _seed_member(maker, 5013, name="Сномером", phone="+998907654321")

    resp = await _get(client, _admin_token(admin_id), filter="all")
    row = next(r for r in resp.json()["items"] if r["name"] == "Сномером")
    assert row["phone"] == "+998907654321"


# --------------------------------------------------------------------------- #
# T2/T3/T5c — where the tickets are now, for ONE draw only
# --------------------------------------------------------------------------- #


async def test_frozen_tickets_report_their_draw_and_numbers(client, maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 5020)
    await _seed_member(maker, 5021, name="Взаморозке")
    async with maker() as s:
        await commit_draw(s, expected_tickets=1, conducted_on=date(2026, 9, 1))

    resp = await _get(client, _admin_token(admin_id), filter="all")
    row = next(r for r in resp.json()["items"] if r["name"] == "Взаморозке")
    assert row["tickets_clean"] == 0
    assert row["draw_number"] == 1
    assert row["numbers"] == [1]


async def test_after_the_draw_the_member_is_marked_played_and_won(
    client, maker, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 5030)
    await _seed_member(maker, 5031, name="Победитель")
    async with maker() as s:
        outcome = await commit_draw(s, expected_tickets=1, conducted_on=None)
    async with maker() as s:
        await record_winners(s, draw_id=outcome.draw_id, winning_numbers=[1])

    resp = await _get(client, _admin_token(admin_id), filter="all")
    row = next(r for r in resp.json()["items"] if r["name"] == "Победитель")
    assert row["played"] == 1
    assert row["won"] is True


async def test_numbers_belong_to_one_draw_not_the_whole_history(client, maker, monkeypatch) -> None:
    """One draw per row (spec decision 6): a member in draw 1 (drawn) and draw 2 (live) reports
    only draw 2 -- "where are they now" must have one answer. Gluing numbers across the whole
    history makes the column unreadable and the acceptance criterion unverifiable."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 5040)
    user_id = await _seed_member(maker, 5041, name="Двухтиражный")
    async with maker() as s:
        first = await commit_draw(s, expected_tickets=1, conducted_on=None)
    async with maker() as s:
        await record_winners(s, draw_id=first.draw_id, winning_numbers=[1])
    async with maker() as s, s.begin():
        s.add(
            LotteryTicket(
                user_id=user_id,
                draw_id=None,
                ticket_number=None,
                source=LotteryTicketSource.ACTIVATION.value,
            )
        )
    async with maker() as s:
        second = await commit_draw(s, expected_tickets=1, conducted_on=None)

    resp = await _get(client, _admin_token(admin_id), filter="all")
    row = next(r for r in resp.json()["items"] if r["name"] == "Двухтиражный")
    assert row["draw_number"] == second.draw_number
    assert row["numbers"] == [1]
    assert row["tickets_total"] == 2


# --------------------------------------------------------------------------- #
# T4 — will these tickets play, and if not, why
# --------------------------------------------------------------------------- #


async def test_unconfirmed_adult_is_named_by_age_not_survey(client, maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 5050)
    await _seed_member(maker, 5051, name="Безвозраста", age=None, is_adult=False)

    resp = await _get(client, _admin_token(admin_id), filter="all")
    row = next(r for r in resp.json()["items"] if r["name"] == "Безвозраста")
    assert row["eligible_now"] is False
    assert row["not_eligible_reason"] == "age"


async def test_missing_city_is_named_by_survey(client, maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 5052)
    await _seed_member(maker, 5053, name="Безгорода", city=None)

    resp = await _get(client, _admin_token(admin_id), filter="all")
    row = next(r for r in resp.json()["items"] if r["name"] == "Безгорода")
    assert row["eligible_now"] is False
    assert row["not_eligible_reason"] == "survey"


async def test_eligible_member_has_no_reason(client, maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 5054)
    await _seed_member(maker, 5055, name="Пригодный")

    resp = await _get(client, _admin_token(admin_id), filter="all")
    row = next(r for r in resp.json()["items"] if r["name"] == "Пригодный")
    assert row["eligible_now"] is True
    assert row["not_eligible_reason"] is None


# --------------------------------------------------------------------------- #
# T5 — the filter and the dashboard counter are the same rule
# --------------------------------------------------------------------------- #


async def test_owed_filter_equals_the_dashboard_detector(client, maker, monkeypatch) -> None:
    """The list and the counter behind the third card must agree on a seeded matrix.

    Not a formality: before this endpoint existed the counter WAS the only expression of the rule,
    and its SQL had already drifted from the Python predicates once (debt of step 2).
    """
    from src.analytics.service import get_loyalty_stats

    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 5060)
    # eligible, no survey ticket → owed
    await _seed_member(maker, 5061, name="Ждёт1", sources=())
    await _seed_member(maker, 5062, name="Ждёт2", sources=(LotteryTicketSource.ACTIVATION,))
    # eligible WITH a survey ticket → not owed
    await _seed_member(maker, 5063, name="Неждёт", sources=(LotteryTicketSource.SURVEY,))
    # not eligible (no city) → not owed even without a ticket
    await _seed_member(maker, 5064, name="Непригоден", city=None, sources=())
    # age below the floor → not owed
    await _seed_member(maker, 5065, name="Несовершеннолетний", age=15, sources=())

    resp = await _get(client, _admin_token(admin_id), filter="owed", size=100)
    listed = {r["name"] for r in resp.json()["items"]}
    async with maker() as s:
        stats = await get_loyalty_stats(s)

    assert listed == {"Ждёт1", "Ждёт2"}
    assert resp.json()["total"] == stats.eligible_without_survey_ticket
    # A vacuous agreement at zero would prove nothing.
    assert stats.eligible_without_survey_ticket == 2


async def test_owed_orders_by_last_activation_with_nulls_last(client, maker, monkeypatch) -> None:
    """The alarm moved from the card's colour into the top row of this list (spec decision 3).

    PostgreSQL puts NULLs FIRST on ``ORDER BY … DESC``. Entry without a QR exists since Stage 4, so
    never-activated members are real and would occupy exactly the rows where a genuine issuance
    failure must appear. The NULL profile below is mandatory — a matrix of merely-old dates stays
    green against the broken ordering.
    """
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 5070)
    now = datetime.now(UTC)
    await _seed_member(maker, 5071, name="Никогда", last_activation_at=None, sources=())
    await _seed_member(
        maker, 5072, name="Месяц назад", last_activation_at=now - timedelta(days=30), sources=()
    )
    await _seed_member(maker, 5073, name="Сегодня", last_activation_at=now, sources=())

    resp = await _get(client, _admin_token(admin_id), filter="owed", size=100)
    order = [r["name"] for r in resp.json()["items"]]
    assert order == ["Сегодня", "Месяц назад", "Никогда"]


# --------------------------------------------------------------------------- #
# T6 — filters and pagination
# --------------------------------------------------------------------------- #


async def test_clean_filter_hides_members_whose_tickets_all_played(
    client, maker, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 5080)
    await _seed_member(maker, 5081, name="Отыграл")
    async with maker() as s:
        outcome = await commit_draw(s, expected_tickets=1, conducted_on=None)
    async with maker() as s:
        await record_winners(s, draw_id=outcome.draw_id, winning_numbers=[1])
    await _seed_member(maker, 5082, name="Наруках")

    resp = await _get(client, _admin_token(admin_id), filter="clean", size=100)
    names = {r["name"] for r in resp.json()["items"]}
    assert "Наруках" in names
    assert "Отыграл" not in names


async def test_all_filter_lists_only_ticket_holders(client, maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 5090)
    await _seed_member(maker, 5091, name="Сбилетом")
    await _seed_member(maker, 5092, name="Безбилета", sources=())

    resp = await _get(client, _admin_token(admin_id), filter="all", size=100)
    names = {r["name"] for r in resp.json()["items"]}
    assert "Сбилетом" in names
    assert "Безбилета" not in names


async def test_pagination_splits_and_keeps_a_stable_order(client, maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 5100)
    for i in range(25):
        await _seed_member(maker, 5200 + i, name=f"Участник{i:02d}")

    first = await _get(client, _admin_token(admin_id), filter="all", page=1, size=20)
    second = await _get(client, _admin_token(admin_id), filter="all", page=2, size=20)

    assert first.json()["total"] == 25
    assert len(first.json()["items"]) == 20
    assert len(second.json()["items"]) == 5
    ids_first = [r["user_id"] for r in first.json()["items"]]
    ids_second = [r["user_id"] for r in second.json()["items"]]
    assert len(set(ids_first) & set(ids_second)) == 0


async def test_rejects_an_unknown_filter(client, maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 5110)
    resp = await _get(client, _admin_token(admin_id), filter="whatever")
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# T8 — one aggregate per page, not one query per row
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("holders", [3, 12])
async def test_query_count_does_not_grow_with_rows(
    client, maker, monkeypatch, holders: int
) -> None:
    """No project-wide helper for this existed (checked); the listener stays local to this file
    until a second consumer appears — an abstraction built for a hypothetical caller is one more
    thing to keep true."""
    from sqlalchemy import event

    monkeypatch.setattr(settings, "loyalty_enabled", True)
    base = 5400 + holders * 100
    for i in range(holders):
        await _seed_member(maker, base + i, name=f"Держатель{i:02d}")

    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    statements: list[str] = []

    def _count(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _count)
    try:
        from src.loyalty import lottery

        sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sm() as s:
            outcome = await lottery.list_ticket_holders(s, page=1, size=50, filter_by="all")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _count)
        await engine.dispose()

    assert len(outcome.items) == holders
    # Relevant draw + total + page = three, whatever the row count.
    assert len(statements) <= 4, f"{len(statements)} queries for {holders} holders: {statements}"
