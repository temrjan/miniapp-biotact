"""Exporting a frozen draw (Stage 5, PR-5.4a): the published list must rehash to the published hash.

The whole point of the endpoint is that an outsider runs ``sha256sum`` over the downloaded file and
gets the value we published at commit. So the assertions here hash **the bytes of the HTTP
response**, never an internal object: a test that hashes what the service returned would pass even
if the HTTP layer re-encoded, wrapped or newline-terminated the body on the way out.

Seeding and freezing cannot share the ``test_db`` session, because ``commit_draw`` owns an explicit
transaction — a second sessionmaker is used, mirroring ``test_lottery_commit.py``.
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest_asyncio
from jose import jwt
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.loyalty.lottery import commit_draw
from src.loyalty.models import CustomerProfile, Gender, LotteryTicket, LoyaltyStatus
from src.users.models import User

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"
AS_OF = datetime(2026, 8, 1, 10, 0, tzinfo=UTC)
PERIOD = "2026-07"


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> object:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


# Local copies of the admin helpers from ``test_lottery_commit.py``. Deliberately duplicated rather
# than hoisted into ``conftest.py``: this is the second use site, and the project standard extracts
# an abstraction at the third — moving them now would edit a file every test module imports, for no
# behaviour gain.
def _admin_token(user_id: int) -> str:
    """Admin token in the shape `/admin/auth/login` issues (`create_admin_token`)."""
    payload = {
        "sub": str(user_id),
        "telegram_id": 1,
        "type": "admin",
        "exp": int((datetime.now(UTC) + timedelta(minutes=30)).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)


def _user_token(user_id: int) -> str:
    """A non-admin token signed with the SAME key — isolates the role check from the signature."""
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
    status: LoyaltyStatus = LoyaltyStatus.NEWBIE,
) -> int:
    """Commit an eligible member; returns user id."""
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
                status=status,
                last_activation_at=AS_OF - timedelta(days=2),
            )
        )
        return user.id


async def _freeze(sm: async_sessionmaker[AsyncSession]) -> tuple[int, str, int, int]:
    """Freeze a two-member draw (one a friend → 3 tickets).

    Returns ``(draw_id, published_hash, total_tickets, second_member_id)``. The second member's id
    is handed back so a tamper test can reassign a ticket to a REAL person: ``user_id`` carries a
    foreign key, so an invented id is rejected by the database before it can model anything.
    """
    await _seed_member(sm, 91001)
    second_id = await _seed_member(sm, 91002, status=LoyaltyStatus.BIOTACT_FRIEND)
    async with sm() as s:
        outcome = await commit_draw(s, period=PERIOD, as_of=AS_OF)
    return outcome.draw_id, outcome.participant_hash, outcome.total_tickets, second_id


def _url(draw_id: int) -> str:
    return f"/api/v1/admin/lottery/{draw_id}/export"


# --------------------------------------------------------------------------- #
# Hash contract — the reason this endpoint exists
# --------------------------------------------------------------------------- #


async def test_response_bytes_rehash_to_the_published_hash(client, maker, monkeypatch) -> None:
    """`sha256sum <downloaded file>` must equal the hash published at commit. Hash the BYTES."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 91000)
    draw_id, published_hash, _, _ = await _freeze(maker)

    resp = await client.get(
        _url(draw_id), headers={"Authorization": f"Bearer {_admin_token(admin_id)}"}
    )

    assert resp.status_code == 200, resp.text
    assert sha256(resp.content).hexdigest() == published_hash


async def test_body_is_byte_exact(client, maker, monkeypatch) -> None:
    """One line per ticket, ascending numbers, `;` separators, no trailing newline, no BOM."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 91010)
    draw_id, _, total, _ = await _freeze(maker)

    resp = await client.get(
        _url(draw_id), headers={"Authorization": f"Bearer {_admin_token(admin_id)}"}
    )

    body = resp.content
    assert not body.startswith(b"\xef\xbb\xbf")  # a BOM would break every external sha256sum
    assert not body.endswith(b"\n")  # a trailing newline would too
    lines = body.decode("utf-8").split("\n")
    assert len(lines) == total
    numbers = [int(line.split(";")[0]) for line in lines]
    assert numbers == list(range(1, total + 1))
    assert all(len(line.split(";")) == 3 for line in lines)


async def test_served_as_a_downloadable_text_file(client, maker, monkeypatch) -> None:
    """Delivered as a file, not as JSON: copy-pasting text is what breaks external verification."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 91020)
    draw_id, _, _, _ = await _freeze(maker)

    resp = await client.get(
        _url(draw_id), headers={"Authorization": f"Bearer {_admin_token(admin_id)}"}
    )

    assert resp.headers["content-type"] == "text/plain; charset=utf-8"
    assert resp.headers["content-disposition"] == f'attachment; filename="draw-{PERIOD}.txt"'


# --------------------------------------------------------------------------- #
# Integrity — never publish a list that disagrees with the published hash
# --------------------------------------------------------------------------- #


async def test_refuses_when_a_ticket_was_reassigned_to_another_person(
    client, maker, monkeypatch
) -> None:
    """Ticket 1 quietly moved to another real member → 409, body withheld.

    This is the exact attack ``user_id`` was put inside the hashed line to make detectable: the
    numbers stay 1..N, the count stays the same, only WHO holds a number changes. Serving the file
    anyway would publish a list that fails our own published hash — from outside indistinguishable
    from a faked draw.
    """
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 91030)
    draw_id, _, _, other_member_id = await _freeze(maker)

    async with maker() as s, s.begin():
        await s.execute(
            update(LotteryTicket)
            .where(LotteryTicket.ticket_number == 1)
            .values(user_id=other_member_id)
        )

    resp = await client.get(
        _url(draw_id), headers={"Authorization": f"Bearer {_admin_token(admin_id)}"}
    )

    assert resp.status_code == 409, resp.text


async def test_refuses_when_a_ticket_number_changed_after_the_freeze(
    client, maker, monkeypatch
) -> None:
    """Renumbering a frozen ticket → 409. ``ticket_number`` carries no FK, so 999999 is reachable."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 91035)
    draw_id, _, _, _ = await _freeze(maker)

    async with maker() as s, s.begin():
        await s.execute(
            update(LotteryTicket)
            .where(LotteryTicket.ticket_number == 1)
            .values(ticket_number=999999)
        )

    resp = await client.get(
        _url(draw_id), headers={"Authorization": f"Bearer {_admin_token(admin_id)}"}
    )

    assert resp.status_code == 409, resp.text


async def test_winner_marks_do_not_affect_the_export(client, maker, monkeypatch) -> None:
    """`is_winner`/`prize_rank` are outside the hash — marking winners (PR-5.4b) must not break it."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 91040)
    draw_id, published_hash, _, _ = await _freeze(maker)

    async with maker() as s, s.begin():
        await s.execute(
            update(LotteryTicket)
            .where(LotteryTicket.ticket_number == 1)
            .values(is_winner=True, prize_rank=1)
        )

    resp = await client.get(
        _url(draw_id), headers={"Authorization": f"Bearer {_admin_token(admin_id)}"}
    )

    assert resp.status_code == 200, resp.text
    assert sha256(resp.content).hexdigest() == published_hash


# --------------------------------------------------------------------------- #
# Boundaries and access
# --------------------------------------------------------------------------- #


async def test_unknown_draw_returns_404(client, maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 91050)

    resp = await client.get(
        _url(4242), headers={"Authorization": f"Bearer {_admin_token(admin_id)}"}
    )

    assert resp.status_code == 404, resp.text


async def test_endpoint_dark_returns_404(client) -> None:
    """loyalty_enabled off → 404 before auth: the surface is not probeable while dark."""
    resp = await client.get(_url(1))
    assert resp.status_code == 404


async def test_endpoint_requires_a_token(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    resp = await client.get(_url(1))
    assert resp.status_code == 401


async def test_endpoint_refuses_a_non_admin_token(client, maker, monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    user_id = await _seed_member(maker, 91060)
    resp = await client.get(
        _url(1), headers={"Authorization": f"Bearer {_user_token(user_id)}"}
    )
    assert resp.status_code == 403, resp.text


async def test_endpoint_refuses_admin_typed_token_of_a_plain_user(
    client, maker, monkeypatch
) -> None:
    """Admin-typed token whose subject is NOT an admin in the DB → refused.

    This is what distinguishes `require_admin` from the lightweight `verify_admin_jwt`, which would
    accept this token: swapping the dependency must redden here.
    """
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    user_id = await _seed_member(maker, 91070)
    resp = await client.get(
        _url(1), headers={"Authorization": f"Bearer {_admin_token(user_id)}"}
    )
    assert resp.status_code == 403, resp.text
