"""Recording winners (Stage 5, PR-5.4b): one prize per PERSON, all-or-nothing, terminal draw.

The numbers come from a human typing what random.org just said on air. This module tests that the
code accepts what the outside world produced and refuses what would contradict the ratified rule -
it never picks anyone itself.

Ratified 2026-07-28: one person takes at most one prize, and a winner is excluded from later rounds
as a PERSON, not merely as a ticket. A number belonging to someone who already won is REFUSED (not
silently skipped) so the host draws again in front of the audience.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
import pytest_asyncio
from jose import jwt
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.common.exceptions import ConflictException
from src.config import settings
from src.loyalty.lottery import DrawOutcome, commit_draw, record_winners
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


def _now() -> datetime:
    return datetime.now(UTC)


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


async def _seed_member(
    sm: async_sessionmaker[AsyncSession],
    telegram_id: int,
    *,
    name: str = "Member",
    tickets: int = 1,
) -> int:
    """An eligible member holding ``tickets`` clean tickets; returns user id."""
    sources = (LotteryTicketSource.SURVEY.value, LotteryTicketSource.FRIEND.value)
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name=name)
        s.add(user)
        await s.flush()
        s.add(CustomerProfile(user_id=user.id, gender=Gender.MALE, age=30, city="Ташкент"))
        for source in sources[:tickets]:
            s.add(
                LotteryTicket(
                    user_id=user.id, draw_id=None, ticket_number=None, source=source
                )
            )
        return user.id


async def _freeze(sm: async_sessionmaker[AsyncSession], *, expected: int) -> int:
    async with sm() as s:
        outcome = await commit_draw(s, expected_tickets=expected, as_of=_now())
    return outcome.draw_id


async def _numbers_of(sm: async_sessionmaker[AsyncSession], user_id: int) -> list[int]:
    async with sm() as s:
        rows = (
            await s.execute(
                select(LotteryTicket.ticket_number)
                .where(LotteryTicket.user_id == user_id)
                .order_by(LotteryTicket.ticket_number)
            )
        ).scalars()
        return list(rows)


async def _winner_count(sm: async_sessionmaker[AsyncSession]) -> int:
    async with sm() as s:
        return int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(LotteryTicket)
                    .where(LotteryTicket.is_winner.is_(True))
                )
            ).scalar_one()
        )


async def _draw_status(sm: async_sessionmaker[AsyncSession]) -> str:
    async with sm() as s:
        return str((await s.execute(select(LotteryDraw.status))).scalar_one())


def _url(draw_id: int) -> str:
    return f"/api/v1/admin/lottery/{draw_id}/winners"


def _enable(monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)


# --------------------------------------------------------------------------- #
# The ratified rule: one prize per PERSON
# --------------------------------------------------------------------------- #


async def test_a_second_ticket_of_the_same_person_is_refused(client, maker, monkeypatch) -> None:
    """A friend holds two tickets. The first wins; their second number must be refused.

    This is the ratified rule in its sharpest form: excluding only the TICKET would let this person
    take two prizes, and "one person - one prize" would not hold.
    """
    _enable(monkeypatch)
    admin_id = await _seed_admin(maker, 93000)
    friend_id = await _seed_member(maker, 93001, tickets=2)
    draw_id = await _freeze(maker, expected=2)
    first, second = await _numbers_of(maker, friend_id)

    resp = await client.post(
        _url(draw_id),
        json={"winning_numbers": [first, second]},
        headers={"Authorization": f"Bearer {_admin_token(admin_id)}"},
    )

    assert resp.status_code == 409, resp.text
    assert str(second) in resp.text, "the host needs to know WHICH number was refused"


async def test_the_rule_does_not_depend_on_the_order_of_entry(client, maker, monkeypatch) -> None:
    """Same pair entered the other way round is refused too - order must not change the outcome."""
    _enable(monkeypatch)
    admin_id = await _seed_admin(maker, 93010)
    friend_id = await _seed_member(maker, 93011, tickets=2)
    draw_id = await _freeze(maker, expected=2)
    first, second = await _numbers_of(maker, friend_id)

    resp = await client.post(
        _url(draw_id),
        json={"winning_numbers": [second, first]},
        headers={"Authorization": f"Bearer {_admin_token(admin_id)}"},
    )

    assert resp.status_code == 409, resp.text


async def test_two_different_people_are_recorded_with_ranks(client, maker, monkeypatch) -> None:
    """POSITIVE CONTROL: distinct winners get ranks by position - first entered is the main prize."""
    _enable(monkeypatch)
    admin_id = await _seed_admin(maker, 93020)
    a_id = await _seed_member(maker, 93021, name="Азиза")
    b_id = await _seed_member(maker, 93022, name="Бекзод")
    draw_id = await _freeze(maker, expected=2)
    (a_number,) = await _numbers_of(maker, a_id)
    (b_number,) = await _numbers_of(maker, b_id)

    resp = await client.post(
        _url(draw_id),
        json={"winning_numbers": [b_number, a_number]},
        headers={"Authorization": f"Bearer {_admin_token(admin_id)}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "drawn"
    assert [w["prize_rank"] for w in body["winners"]] == [1, 2]
    assert [w["ticket_number"] for w in body["winners"]] == [b_number, a_number]
    # The name is what makes the admin's manual lookup possible: the customers list searches by
    # name, never by internal id (Captain's decision 2026-07-28 - no phone in this response).
    assert [w["first_name"] for w in body["winners"]] == ["Бекзод", "Азиза"]
    assert [w["user_id"] for w in body["winners"]] == [b_id, a_id]


# --------------------------------------------------------------------------- #
# All-or-nothing: a refused list writes NOTHING
# --------------------------------------------------------------------------- #


async def test_a_refused_list_writes_nothing_at_all(client, maker, monkeypatch) -> None:
    """The valid first number must NOT be recorded when a later one is refused.

    409 means "nothing changed" in this project, and for an irreversible operation that has to be
    literal: an administrator seeing an error must not wonder whether half of it went through.
    """
    _enable(monkeypatch)
    admin_id = await _seed_admin(maker, 93030)
    other_id = await _seed_member(maker, 93031)
    friend_id = await _seed_member(maker, 93032, tickets=2)
    draw_id = await _freeze(maker, expected=3)
    (other_number,) = await _numbers_of(maker, other_id)
    first, second = await _numbers_of(maker, friend_id)

    resp = await client.post(
        _url(draw_id),
        json={"winning_numbers": [other_number, first, second]},
        headers={"Authorization": f"Bearer {_admin_token(admin_id)}"},
    )

    assert resp.status_code == 409, resp.text
    assert await _winner_count(maker) == 0, "no ticket may be marked when the list is refused"
    assert await _draw_status(maker) == LotteryDrawStatus.COMMITTED.value


# --------------------------------------------------------------------------- #
# Input boundaries - a human types these live, on air
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "numbers",
    [
        pytest.param([0], id="zero"),
        pytest.param([-1], id="negative"),
        pytest.param([9999], id="above_total"),
    ],
)
async def test_numbers_outside_the_range_are_refused(
    client, maker, monkeypatch, numbers: list[int]
) -> None:
    _enable(monkeypatch)
    admin_id = await _seed_admin(maker, 93040)
    await _seed_member(maker, 93041)
    draw_id = await _freeze(maker, expected=1)

    resp = await client.post(
        _url(draw_id),
        json={"winning_numbers": numbers},
        headers={"Authorization": f"Bearer {_admin_token(admin_id)}"},
    )

    assert resp.status_code == 400, resp.text


async def test_a_repeated_number_in_the_list_is_refused(client, maker, monkeypatch) -> None:
    _enable(monkeypatch)
    admin_id = await _seed_admin(maker, 93050)
    member_id = await _seed_member(maker, 93051)
    draw_id = await _freeze(maker, expected=1)
    (number,) = await _numbers_of(maker, member_id)

    resp = await client.post(
        _url(draw_id),
        json={"winning_numbers": [number, number]},
        headers={"Authorization": f"Bearer {_admin_token(admin_id)}"},
    )

    assert resp.status_code == 400, resp.text


async def test_an_empty_list_is_refused(client, maker, monkeypatch) -> None:
    """Running a draw with no winners is almost certainly a slip, not an intention."""
    _enable(monkeypatch)
    admin_id = await _seed_admin(maker, 93060)
    await _seed_member(maker, 93061)
    draw_id = await _freeze(maker, expected=1)

    resp = await client.post(
        _url(draw_id),
        json={"winning_numbers": []},
        headers={"Authorization": f"Bearer {_admin_token(admin_id)}"},
    )

    assert resp.status_code == 422, resp.text


# --------------------------------------------------------------------------- #
# Draw state - terminal, like the freeze
# --------------------------------------------------------------------------- #


async def test_unknown_draw_returns_404(client, maker, monkeypatch) -> None:
    _enable(monkeypatch)
    admin_id = await _seed_admin(maker, 93070)

    resp = await client.post(
        _url(4242),
        json={"winning_numbers": [1]},
        headers={"Authorization": f"Bearer {_admin_token(admin_id)}"},
    )

    assert resp.status_code == 404, resp.text


async def test_a_second_call_on_a_drawn_draw_changes_nothing(client, maker, monkeypatch) -> None:
    """The draw is terminal: replaying it would rewrite a published result."""
    _enable(monkeypatch)
    admin_id = await _seed_admin(maker, 93080)
    a_id = await _seed_member(maker, 93081)
    b_id = await _seed_member(maker, 93082)
    draw_id = await _freeze(maker, expected=2)
    (a_number,) = await _numbers_of(maker, a_id)
    (b_number,) = await _numbers_of(maker, b_id)
    headers = {"Authorization": f"Bearer {_admin_token(admin_id)}"}

    first = await client.post(_url(draw_id), json={"winning_numbers": [a_number]}, headers=headers)
    assert first.status_code == 200, first.text

    second = await client.post(_url(draw_id), json={"winning_numbers": [b_number]}, headers=headers)

    assert second.status_code == 409, second.text
    # The DIAGNOSIS matters, not only the code. The conditional UPDATE would refuse this call by
    # itself, but with the "another administrator just closed it" message — false for a draw that
    # was closed yesterday. On an irreversible operation a wrong explanation is its own defect, so
    # the entry check that produces the right one has to be guarded by an assertion.
    # (Without this line the mutation "remove the entry status check" survives — it did.)
    assert "нельзя разыграть" in second.text, second.text
    assert await _winner_count(maker) == 1, "the original winner must stand"


async def test_two_administrators_at_once_produce_one_result(
    test_db: AsyncSession, maker
) -> None:
    """Concurrent recording: exactly one succeeds, the other is refused, one set of winners lands.

    Driven at the SERVICE level with two independent sessions, mirroring the freeze race tests. Not
    through the HTTP client on purpose: that fixture hands both requests the SAME session, so the
    second coroutine dies on "concurrent operations on one session" and the collision never reaches
    the database. That version passed for the wrong reason and was replaced.

    The serializer is the conditional UPDATE (``WHERE status='committed'``), not a row lock: the
    transition is real here, unlike the freeze where the row was terminal from birth.
    """
    a_id = await _seed_member(maker, 93091)
    b_id = await _seed_member(maker, 93092)
    draw_id = await _freeze(maker, expected=2)
    (a_number,) = await _numbers_of(maker, a_id)
    (b_number,) = await _numbers_of(maker, b_id)

    async with maker() as s1, maker() as s2:
        results = await asyncio.gather(
            record_winners(s1, draw_id=draw_id, winning_numbers=[a_number]),
            record_winners(s2, draw_id=draw_id, winning_numbers=[b_number]),
            return_exceptions=True,
        )

    conflicts = [r for r in results if isinstance(r, ConflictException)]
    successes = [r for r in results if isinstance(r, DrawOutcome)]
    assert len(successes) == 1, results
    assert len(conflicts) == 1, results
    assert await _winner_count(maker) == 1


async def test_a_number_in_range_whose_row_vanished_is_refused(client, maker, monkeypatch) -> None:
    """Number inside 1..total but with no row behind it → 409, not a 500.

    Reachable exactly like the export guard's case: a snapshot row edited after the freeze. Renumber
    ticket 1 away and number 1 is still "in range" (``total_tickets`` is not recomputed) while the
    row is gone. Same threat as the export hash guard, different signal — absence instead of
    mismatch. Technique borrowed from ``test_lottery_export.py`` (Reviewer's pointer at Gate-2).
    """
    _enable(monkeypatch)
    admin_id = await _seed_admin(maker, 93130)
    await _seed_member(maker, 93131)
    draw_id = await _freeze(maker, expected=1)

    async with maker() as s, s.begin():
        await s.execute(
            update(LotteryTicket)
            .where(LotteryTicket.ticket_number == 1)
            .values(ticket_number=999999)
        )

    resp = await client.post(
        _url(draw_id),
        json={"winning_numbers": [1]},
        headers={"Authorization": f"Bearer {_admin_token(admin_id)}"},
    )

    assert resp.status_code == 409, resp.text
    assert "повреждён" in resp.text, resp.text
    assert await _winner_count(maker) == 0


# --------------------------------------------------------------------------- #
# The fairness contract survives the recording
# --------------------------------------------------------------------------- #


async def test_recording_winners_does_not_break_the_published_hash(
    client, maker, monkeypatch
) -> None:
    """`is_winner`/`prize_rank` are outside the hash - the export must still verify afterwards."""
    _enable(monkeypatch)
    admin_id = await _seed_admin(maker, 93100)
    member_id = await _seed_member(maker, 93101)
    draw_id = await _freeze(maker, expected=1)
    (number,) = await _numbers_of(maker, member_id)
    headers = {"Authorization": f"Bearer {_admin_token(admin_id)}"}

    async with maker() as s:
        published = (await s.execute(select(LotteryDraw.participant_hash))).scalar_one()

    await client.post(_url(draw_id), json={"winning_numbers": [number]}, headers=headers)

    export = await client.get(f"/api/v1/admin/lottery/{draw_id}/export", headers=headers)
    assert export.status_code == 200, export.text
    assert sha256(export.content).hexdigest() == published


# --------------------------------------------------------------------------- #
# Access
# --------------------------------------------------------------------------- #


async def test_endpoint_dark_returns_404(client) -> None:
    resp = await client.post(_url(1), json={"winning_numbers": [1]})
    assert resp.status_code == 404


async def test_endpoint_requires_a_token(client, monkeypatch) -> None:
    _enable(monkeypatch)
    resp = await client.post(_url(1), json={"winning_numbers": [1]})
    assert resp.status_code == 401


async def test_endpoint_refuses_a_non_admin_token(client, maker, monkeypatch) -> None:
    _enable(monkeypatch)
    user_id = await _seed_member(maker, 93110)
    resp = await client.post(
        _url(1),
        json={"winning_numbers": [1]},
        headers={"Authorization": f"Bearer {_user_token(user_id)}"},
    )
    assert resp.status_code == 403, resp.text


async def test_endpoint_refuses_admin_typed_token_of_a_plain_user(
    client, maker, monkeypatch
) -> None:
    """Swapping `require_admin` for the lightweight `verify_admin_jwt` must redden here."""
    _enable(monkeypatch)
    user_id = await _seed_member(maker, 93120)
    resp = await client.post(
        _url(1),
        json={"winning_numbers": [1]},
        headers={"Authorization": f"Bearer {_admin_token(user_id)}"},
    )
    assert resp.status_code == 403, resp.text
