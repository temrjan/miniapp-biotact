"""The published fingerprint and the admin surface that produces it (Stage 5).

Two things live here: the byte contract of the canonical snapshot — the half of commit-reveal an
outsider re-computes for themselves — and the authorization boundary of ``POST /admin/lottery/commit``.

The behaviour of the freeze itself (what it stamps, what it refuses, what it does under a race) is
in ``test_lottery_freeze.py``.
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
import pytest_asyncio
from jose import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.loyalty.lottery import canonical_snapshot, snapshot_hash
from src.loyalty.models import (
    CustomerProfile,
    Gender,
    LotteryTicket,
    LotteryTicketSource,
)
from src.users.models import User

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"
COMMIT_URL = "/api/v1/admin/lottery/commit"


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> object:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


# --------------------------------------------------------------------------- #
# Hash contract — byte-exact, reproducible by an outsider
# --------------------------------------------------------------------------- #


def test_canonical_snapshot_is_byte_exact() -> None:
    """The serialization is the published contract: separators, order, no trailing newline."""
    rows = [(2, 77, "friend"), (1, 77, "survey"), (3, 91, "survey")]
    assert canonical_snapshot(rows) == "1;77;survey\n2;77;friend\n3;91;survey"


def test_hash_is_reproducible_by_an_independent_recount() -> None:
    """An outsider recomputing SHA-256 over the published list must get the published value."""
    rows = [(1, 5, "survey"), (2, 5, "friend")]
    independently = sha256(b"1;5;survey\n2;5;friend").hexdigest()
    assert snapshot_hash(rows) == independently


def test_snapshot_order_does_not_depend_on_input_order() -> None:
    """Rows arriving in any order hash the same — the contract sorts by ticket number."""
    rows = [(1, 5, "survey"), (2, 5, "friend"), (3, 9, "survey")]
    assert snapshot_hash(list(reversed(rows))) == snapshot_hash(rows)


@pytest.mark.parametrize(
    "tampered",
    [
        pytest.param([(1, 5, "survey"), (2, 6, "friend")], id="user_swapped"),
        pytest.param([(1, 5, "survey"), (3, 5, "friend")], id="number_changed"),
        pytest.param([(1, 5, "survey"), (2, 5, "survey")], id="source_changed"),
    ],
)
def test_any_ticket_change_changes_the_hash(tampered: list[tuple[int, int, str]]) -> None:
    """Tampering with a single field after publication must be detectable."""
    original = [(1, 5, "survey"), (2, 5, "friend")]
    assert snapshot_hash(tampered) != snapshot_hash(original)


def test_the_new_source_is_inside_the_hashed_line() -> None:
    """A purchase ticket hashes differently from a survey ticket of the same person and number.

    ``activation`` joined the sources in step 2 and rides the existing contract rather than
    extending it; this pins that it actually reaches the hashed bytes.
    """
    assert snapshot_hash([(1, 5, "activation")]) != snapshot_hash([(1, 5, "survey")])


# --------------------------------------------------------------------------- #
# Admin surface — authorization boundary and the confirmation
# --------------------------------------------------------------------------- #


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


async def _seed_member_with_ticket(
    sm: async_sessionmaker[AsyncSession], telegram_id: int
) -> int:
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


async def test_endpoint_dark_returns_404(client) -> None:
    """loyalty_enabled off → 404 before auth: the surface is not probeable while dark."""
    resp = await client.post(COMMIT_URL, json={"expected_tickets": 1})
    assert resp.status_code == 404


async def test_endpoint_requires_a_token(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    resp = await client.post(COMMIT_URL, json={"expected_tickets": 1})
    assert resp.status_code == 401  # no credentials at all — refused before the role is looked up


async def test_endpoint_refuses_a_non_admin_token(client, maker, monkeypatch) -> None:
    """A valid NON-admin token must not freeze a draw — the role is checked in the database."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    user_id = await _seed_member_with_ticket(maker, 90020)
    headers = {"Authorization": f"Bearer {_user_token(user_id)}"}
    resp = await client.post(COMMIT_URL, json={"expected_tickets": 1}, headers=headers)
    assert resp.status_code == 403, resp.text


async def test_endpoint_refuses_admin_typed_token_of_a_plain_user(
    client, maker, monkeypatch
) -> None:
    """Admin-typed token whose subject is NOT an admin in the DB → refused (this is why we hit it)."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    user_id = await _seed_member_with_ticket(maker, 90021)
    headers = {"Authorization": f"Bearer {_admin_token(user_id)}"}
    resp = await client.post(COMMIT_URL, json={"expected_tickets": 1}, headers=headers)
    assert resp.status_code == 403, resp.text


async def test_endpoint_admin_freezes_the_draw(client, maker, monkeypatch) -> None:
    """POSITIVE CONTROL: a real admin freezes and gets the published numbers back."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 90022)
    await _seed_member_with_ticket(maker, 90023)
    headers = {"Authorization": f"Bearer {_admin_token(admin_id)}"}

    resp = await client.post(
        COMMIT_URL,
        json={"expected_tickets": 1, "prize_label": "Набор BIOTACT", "conducted_on": "2026-09-01"},
        headers=headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["draw_number"] == 1
    assert body["total_tickets"] == 1
    assert body["conducted_on"] == "2026-09-01"
    assert len(body["participant_hash"]) == 64


async def test_endpoint_requires_the_confirmation_number(client, maker, monkeypatch) -> None:
    """A body without ``expected_tickets`` is refused at the edge — the confirmation is not optional.

    An optional confirmation is not a confirmation: the whole point of the ratified protection is
    that an irreversible operation cannot be triggered by a body that carries no intent.
    """
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 90024)
    headers = {"Authorization": f"Bearer {_admin_token(admin_id)}"}
    resp = await client.post(COMMIT_URL, json={"prize_label": "без числа"}, headers=headers)
    assert resp.status_code == 422


async def test_endpoint_reports_the_actual_count_when_the_confirmation_is_wrong(
    client, maker, monkeypatch
) -> None:
    """The refusal has to tell the administrator what the right number is, not just say no."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    admin_id = await _seed_admin(maker, 90025)
    await _seed_member_with_ticket(maker, 90026)
    headers = {"Authorization": f"Bearer {_admin_token(admin_id)}"}

    resp = await client.post(COMMIT_URL, json={"expected_tickets": 7}, headers=headers)

    assert resp.status_code == 409, resp.text
    assert "1" in resp.json()["detail"]
