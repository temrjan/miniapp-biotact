"""Admin surface of the two draw actions added in step 3: cancelling, and the announced date.

Both were built without an HTTP boundary of their own and the date without any test at all — found
at Gate-2 (МИНОР-2, МИНОР-3), and both are exactly the kind of gap that stays invisible: losing
``Depends(require_admin)`` on the cancel endpoint would not have reddened a single line, while that
endpoint releases a published draw.

The freeze has its own boundary block in ``test_lottery_commit.py``; the behaviour of cancelling is
in ``test_lottery_cancel.py``. This file is about who may call these two, and what they answer.
"""

from datetime import UTC, date, datetime, timedelta

import jwt
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.common.exceptions import ConflictException, NotFoundException
from src.config import settings
from src.loyalty.lottery import cancel_draw, commit_draw, set_conducted_on
from src.loyalty.models import (
    CustomerProfile,
    Gender,
    LotteryDraw,
    LotteryTicket,
    LotteryTicketSource,
)
from src.users.models import User

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"


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


async def _seed_member(sm: async_sessionmaker[AsyncSession], telegram_id: int) -> int:
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Member")
        s.add(user)
        await s.flush()
        s.add(CustomerProfile(user_id=user.id, gender=Gender.MALE, age=30, city="Ташкент"))
        s.add(
            LotteryTicket(
                user_id=user.id,
                draw_id=None,
                ticket_number=None,
                source=LotteryTicketSource.SURVEY.value,
            )
        )
        return user.id


async def _seed_plain_user(sm: async_sessionmaker[AsyncSession], telegram_id: int) -> int:
    """A registered user with no profile and no ticket — only a subject for a token.

    Deliberately NOT a club member: a member would bring a clean ticket of their own, and the freeze
    below would then refuse on a confirmation mismatch instead of testing what this file is about.
    """
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name="Plain")
        s.add(user)
        await s.flush()
        return user.id


async def _frozen(sm: async_sessionmaker[AsyncSession], telegram_id: int) -> int:
    await _seed_member(sm, telegram_id)
    async with sm() as s:
        outcome = await commit_draw(s, expected_tickets=1)
    return outcome.draw_id


def _cancel_url(draw_id: int) -> str:
    return f"/api/v1/admin/lottery/{draw_id}/cancel"


def _draw_url(draw_id: int) -> str:
    return f"/api/v1/admin/lottery/{draw_id}"


# --------------------------------------------------------------------------- #
# POST /{id}/cancel — the boundary (МИНОР-3)
# --------------------------------------------------------------------------- #


async def test_cancel_dark_returns_404(client) -> None:
    """loyalty_enabled off → 404 before auth: the dark surface is not probeable."""
    resp = await client.post(_cancel_url(1))
    assert resp.status_code == 404


async def test_cancel_requires_a_token(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    resp = await client.post(_cancel_url(1))
    assert resp.status_code == 401


async def test_cancel_refuses_a_non_admin_token(client, maker, monkeypatch) -> None:
    """A valid NON-admin token must not release a published draw."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    member = await _seed_plain_user(maker, 94001)
    draw_id = await _frozen(maker, 94002)

    resp = await client.post(
        _cancel_url(draw_id), headers={"Authorization": f"Bearer {_user_token(member)}"}
    )

    assert resp.status_code == 403, resp.text


async def test_cancel_refuses_admin_typed_token_of_a_plain_user(client, maker, monkeypatch) -> None:
    """Admin-TYPED token whose subject is not an admin in the database → refused.

    The role is verified against the database, not taken from the token — which is the whole reason
    the token type alone is not enough.
    """
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    member = await _seed_plain_user(maker, 94011)
    draw_id = await _frozen(maker, 94012)

    resp = await client.post(
        _cancel_url(draw_id), headers={"Authorization": f"Bearer {_admin_token(member)}"}
    )

    assert resp.status_code == 403, resp.text


async def test_cancel_by_a_real_admin_releases_the_tickets(client, maker, monkeypatch) -> None:
    """POSITIVE CONTROL: without it the four refusals above would also pass on a broken endpoint."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 94021)
    draw_id = await _frozen(maker, 94022)

    resp = await client.post(
        _cancel_url(draw_id), headers={"Authorization": f"Bearer {_admin_token(admin_id)}"}
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["draw_number"] == 1
    assert body["released_tickets"] == 1
    async with maker() as s:
        stamped = (await s.execute(select(LotteryTicket.draw_id))).scalars().all()
    assert stamped == [None]


# --------------------------------------------------------------------------- #
# PATCH /{id} — the announced date (МИНОР-2)
# --------------------------------------------------------------------------- #


async def _stored_date(sm: async_sessionmaker[AsyncSession], draw_id: int) -> date | None:
    async with sm() as s:
        return (
            await s.execute(select(LotteryDraw.conducted_on).where(LotteryDraw.id == draw_id))
        ).scalar_one()


async def test_setting_and_clearing_the_date(test_db: AsyncSession, maker) -> None:
    """The date is announced, moved, and can be taken back to "дата уточняется".

    Every step is read back from the DATABASE, not from the return value. A first version checked
    the response and then only the final NULL — and a mutation that dropped the UPDATE altogether
    passed it, because "never written" and "written then cleared" both end at NULL. An assertion
    that cannot fail in the direction that matters is not an assertion.
    """
    draw_id = await _frozen(maker, 94031)
    assert await _stored_date(maker, draw_id) is None, "a draw starts without an announced date"

    async with maker() as s:
        number, stored = await set_conducted_on(s, draw_id=draw_id, conducted_on=date(2026, 9, 1))
    assert (number, stored) == (1, date(2026, 9, 1))
    assert await _stored_date(maker, draw_id) == date(2026, 9, 1)

    async with maker() as s:
        _, moved = await set_conducted_on(s, draw_id=draw_id, conducted_on=date(2026, 9, 15))
    assert moved == date(2026, 9, 15)
    assert await _stored_date(maker, draw_id) == date(2026, 9, 15), "a postponement must persist"

    async with maker() as s:
        _, cleared = await set_conducted_on(s, draw_id=draw_id, conducted_on=None)
    assert cleared is None
    assert await _stored_date(maker, draw_id) is None, "clearing must reach the database too"


async def test_moving_the_date_does_not_touch_the_published_hash(
    test_db: AsyncSession, maker
) -> None:
    """The reason the date is editable at all: it is outside the fingerprint.

    If a postponement could change the hash, every announced draw would become unverifiable the
    moment it moved — and from outside that is indistinguishable from a substituted list.
    """
    draw_id = await _frozen(maker, 94041)
    async with maker() as s:
        before = (
            await s.execute(select(LotteryDraw.participant_hash).where(LotteryDraw.id == draw_id))
        ).scalar_one()

    async with maker() as s:
        await set_conducted_on(s, draw_id=draw_id, conducted_on=date(2026, 12, 31))

    async with maker() as s:
        after = (
            await s.execute(select(LotteryDraw.participant_hash).where(LotteryDraw.id == draw_id))
        ).scalar_one()
    assert after == before


async def test_the_date_of_a_cancelled_draw_cannot_be_set(test_db: AsyncSession, maker) -> None:
    """There is no event left to date, and pretending otherwise would resurrect a cancelled draw."""
    draw_id = await _frozen(maker, 94051)
    async with maker() as s:
        await cancel_draw(s, draw_id=draw_id)

    async with maker() as s:
        with pytest.raises(ConflictException):
            await set_conducted_on(s, draw_id=draw_id, conducted_on=date(2026, 9, 1))

    async with maker() as s:
        stored = (await s.execute(select(LotteryDraw.conducted_on))).scalar_one()
    assert stored is None


async def test_the_date_of_a_draw_that_does_not_exist_is_a_404(
    test_db: AsyncSession, maker
) -> None:
    async with maker() as s:
        with pytest.raises(NotFoundException):
            await set_conducted_on(s, draw_id=4242, conducted_on=date(2026, 9, 1))


async def test_patch_dark_returns_404(client) -> None:
    resp = await client.patch(_draw_url(1), json={"conducted_on": "2026-09-01"})
    assert resp.status_code == 404


async def test_patch_requires_a_token(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    resp = await client.patch(_draw_url(1), json={"conducted_on": "2026-09-01"})
    assert resp.status_code == 401


async def test_patch_refuses_a_non_admin_token(client, maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    member = await _seed_plain_user(maker, 94061)
    draw_id = await _frozen(maker, 94062)

    resp = await client.patch(
        _draw_url(draw_id),
        json={"conducted_on": "2026-09-01"},
        headers={"Authorization": f"Bearer {_user_token(member)}"},
    )

    assert resp.status_code == 403, resp.text


async def test_patch_by_a_real_admin_announces_the_date(client, maker, monkeypatch) -> None:
    """POSITIVE CONTROL for the endpoint, including the serialization of a date over HTTP."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 94071)
    draw_id = await _frozen(maker, 94072)

    resp = await client.patch(
        _draw_url(draw_id),
        json={"conducted_on": "2026-09-01"},
        headers={"Authorization": f"Bearer {_admin_token(admin_id)}"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"draw_number": 1, "conducted_on": "2026-09-01"}
    assert await _stored_date(maker, draw_id) == date(2026, 9, 1)
