"""Read endpoints behind the admin draw page (Stage 5, step 4): current state and the print list.

No business logic lives here — it all lives in step 3. These tests pin the two things the page
stands on: the number the administrator confirms is the POST-FILTER one (the same function the
freeze takes), and the print list carries the human column (last-4, zero-padded) without ever
leaking the full telegram id. The boundary block is the same five gates every admin endpoint in
this router answers.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
import pytest_asyncio
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.loyalty.lottery import cancel_draw, commit_draw, record_winners
from src.loyalty.models import (
    CustomerProfile,
    Gender,
    LotteryTicket,
    LotteryTicketSource,
)
from src.users.models import User

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"

CURRENT_URL = "/api/v1/admin/lottery/current"


def _print_url(draw_id: int) -> str:
    return f"/api/v1/admin/lottery/{draw_id}/print"


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
    city: str | None = "Ташкент",
    with_ticket: bool = True,
) -> int:
    """An eligible (or deliberately not) member; ``with_ticket`` adds a CLEAN survey ticket."""
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Member")
        s.add(user)
        await s.flush()
        s.add(CustomerProfile(user_id=user.id, gender=Gender.MALE, age=30, city=city))
        if with_ticket:
            s.add(
                LotteryTicket(
                    user_id=user.id,
                    draw_id=None,
                    ticket_number=None,
                    source=LotteryTicketSource.SURVEY.value,
                )
            )
        return user.id


async def _frozen(sm: async_sessionmaker[AsyncSession], telegram_id: int) -> int:
    await _seed_member(sm, telegram_id)
    async with sm() as s:
        outcome = await commit_draw(s, expected_tickets=1, conducted_on=date(2026, 9, 1))
    return outcome.draw_id


# --------------------------------------------------------------------------- #
# Boundary — both endpoints, the same five gates as the rest of this router
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("url", [CURRENT_URL, _print_url(1)])
async def test_dark_returns_404(client, url: str) -> None:
    resp = await client.get(url)
    assert resp.status_code == 404


@pytest.mark.parametrize("url", [CURRENT_URL, _print_url(1)])
async def test_requires_a_token(client, monkeypatch, url: str) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    resp = await client.get(url)
    assert resp.status_code == 401


@pytest.mark.parametrize("url", [CURRENT_URL, _print_url(1)])
async def test_refuses_a_non_admin_token(client, maker, monkeypatch, url: str) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    member = await _seed_plain_user(maker, 95001)
    resp = await client.get(url, headers={"Authorization": f"Bearer {_user_token(member)}"})
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize("url", [CURRENT_URL, _print_url(1)])
async def test_refuses_admin_typed_token_of_a_plain_user(client, maker, monkeypatch, url: str) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    member = await _seed_plain_user(maker, 95011)
    resp = await client.get(url, headers={"Authorization": f"Bearer {_admin_token(member)}"})
    assert resp.status_code == 403, resp.text


# --------------------------------------------------------------------------- #
# GET /admin/lottery/current
# --------------------------------------------------------------------------- #


async def test_current_without_draws_reports_post_filter_numbers(
    client, maker, monkeypatch
) -> None:
    """A1: no draw yet — ``draw`` is null, and the number to confirm is the ELIGIBLE count."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 95021)
    await _seed_member(maker, 95022)

    resp = await client.get(CURRENT_URL, headers={"Authorization": f"Bearer {_admin_token(admin_id)}"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["draw"] is None
    assert body["eligible_tickets"] == 1
    assert body["clean_tickets"] == 1


async def test_current_distinguishes_eligible_from_all_clean(client, maker, monkeypatch) -> None:
    """A2: a clean ticket whose owner fails the survey predicate counts in ``clean`` but NOT in
    ``eligible`` — confirming the dashboard number would fail the freeze (P-3)."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 95031)
    await _seed_member(maker, 95032)
    await _seed_member(maker, 95033, city=None)  # clean ticket, ineligible owner

    resp = await client.get(CURRENT_URL, headers={"Authorization": f"Bearer {_admin_token(admin_id)}"})

    body = resp.json()
    assert body["clean_tickets"] == 2
    assert body["eligible_tickets"] == 1


async def test_current_on_a_committed_draw_carries_the_fingerprint(client, maker, monkeypatch) -> None:
    """A3: the page the administrator publishes from — number, count, date, hash."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 95041)
    await _frozen(maker, 95042)

    resp = await client.get(CURRENT_URL, headers={"Authorization": f"Bearer {_admin_token(admin_id)}"})

    body = resp.json()
    draw = body["draw"]
    assert draw["status"] == "committed"
    assert draw["draw_number"] == 1
    assert draw["total_tickets"] == 1
    assert draw["conducted_on"] == "2026-09-01"
    assert len(draw["participant_hash"]) == 64


async def test_current_on_a_drawn_draw_reads_winners_from_the_ticket_rows(
    client, maker, monkeypatch
) -> None:
    """A4: winners come from the rows, not from a mutation response — the page survives reload."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 95051)
    draw_id = await _frozen(maker, 95052)
    async with maker() as s:
        await record_winners(s, draw_id=draw_id, winning_numbers=[1])

    resp = await client.get(CURRENT_URL, headers={"Authorization": f"Bearer {_admin_token(admin_id)}"})

    draw = resp.json()["draw"]
    assert draw["status"] == "drawn"
    assert len(draw["winners"]) == 1
    winner = draw["winners"][0]
    assert winner["ticket_number"] == 1
    assert winner["prize_rank"] == 1
    assert winner["first_name"] == "Member"


async def test_current_shows_a_cancelled_draw_instead_of_hiding_it(client, maker, monkeypatch) -> None:
    """A5 (P-2): the administrator must publish the cancellation — hiding its object from them
    is how a published fingerprint quietly disappears."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 95061)
    draw_id = await _frozen(maker, 95062)
    async with maker() as s:
        await cancel_draw(s, draw_id=draw_id)

    resp = await client.get(CURRENT_URL, headers={"Authorization": f"Bearer {_admin_token(admin_id)}"})

    draw = resp.json()["draw"]
    assert draw["status"] == "cancelled"
    assert draw["draw_number"] == 1
    assert draw["cancelled_at"] is not None
    assert len(draw["participant_hash"]) == 64


# --------------------------------------------------------------------------- #
# GET /admin/lottery/{draw_id}/print
# --------------------------------------------------------------------------- #


async def test_print_list_numbers_ascending_and_last4_zero_padded(client, maker, monkeypatch) -> None:
    """A6: ``0042`` and ``42`` are the same column a member scans with their eyes — the padding is
    what keeps them looking like one. Full telegram_id never leaves the server."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 95071)
    await _seed_member(maker, 42)  # telegram_id = 42 → last-4 must print as "0042"
    await _seed_member(maker, 95072)
    async with maker() as s:
        outcome = await commit_draw(s, expected_tickets=2)
    draw_id = outcome.draw_id

    resp = await client.get(
        _print_url(draw_id), headers={"Authorization": f"Bearer {_admin_token(admin_id)}"}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["draw_number"] == 1
    assert body["total_tickets"] == 2
    numbers = [row["ticket_number"] for row in body["rows"]]
    assert numbers == sorted(numbers)
    by_number = {row["ticket_number"]: row["telegram_last4"] for row in body["rows"]}
    assert by_number[1] == "0042"
    assert all(len(row["telegram_last4"]) == 4 for row in body["rows"])
    assert "telegram_id" not in resp.text.replace("telegram_last4", "")


async def test_print_of_a_missing_draw_is_a_404(client, maker, monkeypatch) -> None:
    """A7: nothing to print from nothing."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 95081)

    resp = await client.get(
        _print_url(4242), headers={"Authorization": f"Bearer {_admin_token(admin_id)}"}
    )

    assert resp.status_code == 404


async def test_print_of_a_cancelled_draw_refuses_and_points_at_the_journal(
    client, maker, monkeypatch
) -> None:
    """A7+ (уточнение Гейта-1 №1): cancelling releases the stamps, so a print page would render an
    EMPTY list — which from outside reads as tampering, not as a cancellation. The journal on the
    export endpoint is the list of a cancelled draw."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 95091)
    draw_id = await _frozen(maker, 95092)
    async with maker() as s:
        await cancel_draw(s, draw_id=draw_id)

    resp = await client.get(
        _print_url(draw_id), headers={"Authorization": f"Bearer {_admin_token(admin_id)}"}
    )

    assert resp.status_code == 409
    assert "отменён" in resp.json()["detail"]


async def test_print_of_a_drawn_draw_still_works(client, maker, monkeypatch) -> None:
    """The stamps stay after winners are recorded — the list of a finished draw prints."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 95101)
    draw_id = await _frozen(maker, 95102)
    async with maker() as s:
        await record_winners(s, draw_id=draw_id, winning_numbers=[1])

    resp = await client.get(
        _print_url(draw_id), headers={"Authorization": f"Bearer {_admin_token(admin_id)}"}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["total_tickets"] == 1


async def test_current_keeps_the_previous_drawn_draw_reachable_after_the_next_freeze(
    client, maker, monkeypatch
) -> None:
    """A8 (Гейт-2, БЛОКЕР-1): draw №1 drawn, draw №2 frozen — the winners of №1 must still be
    visible: prizes are handed out for days after the next freeze, and "не видно нигде" is not
    an answer to a winner asking who won."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 95111)
    await _seed_member(maker, 95112)
    async with maker() as s:
        first = await commit_draw(s, expected_tickets=1)
        await record_winners(s, draw_id=first.draw_id, winning_numbers=[1])
    await _seed_member(maker, 95113)
    async with maker() as s:
        await commit_draw(s, expected_tickets=1)

    resp = await client.get(CURRENT_URL, headers={"Authorization": f"Bearer {_admin_token(admin_id)}"})

    body = resp.json()
    assert body["draw"]["draw_number"] == 2
    assert body["draw"]["status"] == "committed"
    last_drawn = body["last_drawn"]
    assert last_drawn["draw_number"] == 1
    assert last_drawn["status"] == "drawn"
    assert len(last_drawn["winners"]) == 1
    assert last_drawn["winners"][0]["first_name"] == "Member"


async def test_current_on_a_drawn_latest_draw_does_not_duplicate_it_into_last_drawn(
    client, maker, monkeypatch
) -> None:
    """When the latest draw IS the drawn one, it is the page — `last_drawn` stays null."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 95121)
    draw_id = await _frozen(maker, 95122)
    async with maker() as s:
        await record_winners(s, draw_id=draw_id, winning_numbers=[1])

    resp = await client.get(CURRENT_URL, headers={"Authorization": f"Bearer {_admin_token(admin_id)}"})

    body = resp.json()
    assert body["draw"]["status"] == "drawn"
    assert body["last_drawn"] is None
