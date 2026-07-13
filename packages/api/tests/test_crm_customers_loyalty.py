"""Tests for the Biotact Club enrichment of the admin CRM customers endpoints (PR-2).

First tests of the CRM router at all. Seeding commits through a second sessionmaker on the
same test DB (READ COMMITTED visibility), mirroring test_loyalty_summary.py; the admin JWT
comes from the shared ``admin_headers`` conftest fixture (sub=1 → a committed admin user).
"""

import time

import pytest_asyncio
from httpx import AsyncClient
from jose import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.loyalty.models import CustomerProfile, Gender, LedgerReason, LoyaltyStatus, PointLedger
from src.users.models import User

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"

CUSTOMERS_URL = "/api/v1/crm/customers"
CUSTOMER_URL = "/api/v1/crm/customer/{}"


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> object:
    """A sessionmaker on the same test DB ``test_db`` created — for committed seeding/asserts."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _seed_admin(sm: async_sessionmaker[AsyncSession]) -> None:
    """Commit the admin user the shared ``admin_headers`` fixture points at (sub=1).

    The explicit ``id=1`` bypasses the sequence, so advance it — otherwise the next
    auto-id user collides with the admin row (``users_pkey`` violation).
    """
    async with sm() as s, s.begin():
        s.add(User(id=1, telegram_id=90001, first_name="Admin", is_admin=True))
        await s.flush()
        await s.execute(
            text(
                "SELECT setval(pg_get_serial_sequence('users', 'id'), (SELECT MAX(id) FROM users))"
            )
        )


async def _seed_customer(
    sm: async_sessionmaker[AsyncSession],
    telegram_id: int,
    first_name: str,
    *,
    member: bool = False,
    source: str | None = None,
    lifetime: int = 0,
    gender: Gender | None = None,
    age: int | None = None,
    city: str | None = None,
    ledger: tuple[int, ...] = (),
    symptoms: list[str] | None = None,
) -> int:
    """Commit a user, optionally with a club profile and point-ledger rows; return user id."""
    async with sm() as s, s.begin():
        user = User(telegram_id=telegram_id, first_name=first_name, symptoms=symptoms or [])
        s.add(user)
        await s.flush()
        if member:
            s.add(
                CustomerProfile(
                    user_id=user.id,
                    registration_source=source,
                    status=LoyaltyStatus.NEWBIE,
                    lifetime_activations=lifetime,
                    gender=gender,
                    age=age,
                    city=city,
                )
            )
        for amount in ledger:
            reason = LedgerReason.CASHBACK if amount >= 0 else LedgerReason.REDEEM
            s.add(PointLedger(user_id=user.id, amount=amount, reason=reason))
        return user.id


async def _seed_trio(sm: async_sessionmaker[AsyncSession]) -> None:
    """The canonical page: A = member via QR with survey+ledger, B = member direct, C = plain user."""
    await _seed_admin(sm)
    await _seed_customer(
        sm,
        80001,
        "ClubViaQr",
        member=True,
        source="qr",
        lifetime=2,
        gender=Gender.MALE,
        age=47,
        city="Ташкент",
        ledger=(6000, -500),  # balance must be the SUM, not the last row
    )
    await _seed_customer(sm, 80002, "ClubDirect", member=True, source="direct")
    await _seed_customer(sm, 80003, "PlainShopper")


def _by_telegram_id(body: dict, telegram_id: int) -> dict:
    matches = [item for item in body["items"] if item["telegram_id"] == telegram_id]
    assert len(matches) == 1, f"expected exactly one item for {telegram_id}"
    return matches[0]


# --------------------------------------------------------------------------- #
# enrichment
# --------------------------------------------------------------------------- #


async def test_customers_list_enriched_with_loyalty(
    client: AsyncClient, maker, admin_headers: dict[str, str]
) -> None:
    """Members carry the full club slice with exact values; a plain user carries loyalty=null."""
    await _seed_trio(maker)

    resp = await client.get(CUSTOMERS_URL, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    via_qr = _by_telegram_id(body, 80001)["loyalty"]
    assert via_qr == {
        "source": "qr",
        "status": "newbie",
        "lifetime_activations": 2,
        "gender": "male",
        "age": 47,
        "city": "Ташкент",
        "balance": 5500,  # 6000 - 500: the SUM, not the last row
    }

    direct = _by_telegram_id(body, 80002)["loyalty"]
    assert direct["source"] == "direct"
    assert direct["balance"] == 0  # no ledger rows at all → 0, not an error
    assert (direct["gender"], direct["age"], direct["city"]) == (None, None, None)

    assert _by_telegram_id(body, 80003)["loyalty"] is None


async def test_customer_single_enriched(
    client: AsyncClient, maker, admin_headers: dict[str, str]
) -> None:
    """The single-customer endpoint carries the same club slice; a plain user gets null."""
    await _seed_trio(maker)

    resp = await client.get(CUSTOMER_URL.format(80001), headers=admin_headers)
    assert resp.status_code == 200, resp.text
    loyalty = resp.json()["loyalty"]
    assert loyalty["source"] == "qr"
    assert loyalty["balance"] == 5500

    resp = await client.get(CUSTOMER_URL.format(80003), headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["loyalty"] is None


# --------------------------------------------------------------------------- #
# club filter
# --------------------------------------------------------------------------- #


async def test_customers_filter_members_only(
    client: AsyncClient, maker, admin_headers: dict[str, str]
) -> None:
    """club=member → only users with a profile row; total reflects the FILTERED count."""
    await _seed_trio(maker)

    resp = await client.get(CUSTOMERS_URL, params={"club": "member"}, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert {item["telegram_id"] for item in body["items"]} == {80001, 80002}


async def test_customers_filter_qr_only(
    client: AsyncClient, maker, admin_headers: dict[str, str]
) -> None:
    """club=qr → only members whose registration_source is exactly 'qr'."""
    await _seed_trio(maker)

    resp = await client.get(CUSTOMERS_URL, params={"club": "qr"}, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert [item["telegram_id"] for item in body["items"]] == [80001]


async def test_customers_filter_composes_with_search(
    client: AsyncClient, maker, admin_headers: dict[str, str]
) -> None:
    """club=qr AND search matching only the direct member → empty page, total 0."""
    await _seed_trio(maker)

    resp = await client.get(
        CUSTOMERS_URL, params={"club": "qr", "search": "ClubDirect"}, headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


async def test_customers_rejects_unknown_club_value(
    client: AsyncClient, maker, admin_headers: dict[str, str]
) -> None:
    """A garbage club value fails Literal validation with 422, not a silent full list."""
    await _seed_admin(maker)

    resp = await client.get(CUSTOMERS_URL, params={"club": "vip"}, headers=admin_headers)
    assert resp.status_code == 422


async def test_customer_single_unknown_telegram_id_is_404(
    client: AsyncClient, maker, admin_headers: dict[str, str]
) -> None:
    """An unknown telegram_id on the single-customer endpoint → 404 (first coverage of it)."""
    await _seed_admin(maker)

    resp = await client.get(CUSTOMER_URL.format(999999), headers=admin_headers)
    assert resp.status_code == 404


async def test_customers_page_of_non_members_only(
    client: AsyncClient, maker, admin_headers: dict[str, str]
) -> None:
    """A whole page without a single member → every loyalty is null (empty-profiles branch)."""
    await _seed_admin(maker)
    await _seed_customer(maker, 80010, "PlainOne")

    resp = await client.get(CUSTOMERS_URL, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 2  # admin + PlainOne
    assert all(item["loyalty"] is None for item in items)


async def test_customers_club_composes_with_problem_filter(
    client: AsyncClient, maker, admin_headers: dict[str, str]
) -> None:
    """club=qr AND problem=immunity → only the QR member carrying that symptom."""
    await _seed_admin(maker)
    await _seed_customer(
        maker, 80021, "QrImmunity", member=True, source="qr", symptoms=["immunity"]
    )
    await _seed_customer(maker, 80022, "QrNoSymptoms", member=True, source="qr")

    resp = await client.get(
        CUSTOMERS_URL, params={"club": "qr", "problem": "immunity"}, headers=admin_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert [item["telegram_id"] for item in body["items"]] == [80021]


async def test_customers_filtered_pagination_math(
    client: AsyncClient, maker, admin_headers: dict[str, str]
) -> None:
    """13 members (+1 plain user around) → club=member page=2 holds exactly 1, total honest."""
    await _seed_admin(maker)
    await _seed_customer(maker, 80050, "PlainAround")  # must not leak into the filtered pages
    for i in range(13):
        await _seed_customer(maker, 80100 + i, f"Member{i:02d}", member=True, source="direct")

    resp = await client.get(
        CUSTOMERS_URL,
        params={"club": "member", "page": 2, "size": 12},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 13
    assert body["pages"] == 2
    assert len(body["items"]) == 1
    assert body["items"][0]["loyalty"] is not None


async def test_customer_negative_balance_is_serialized(
    client: AsyncClient, maker, admin_headers: dict[str, str]
) -> None:
    """A ledger summing below zero (future REDEEM) serializes as a negative int, not a clamp."""
    await _seed_admin(maker)
    await _seed_customer(maker, 80060, "Overdrawn", member=True, source="qr", ledger=(100, -600))

    resp = await client.get(CUSTOMER_URL.format(80060), headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["loyalty"]["balance"] == -500


# --------------------------------------------------------------------------- #
# authz — first CRM router tests at all
# --------------------------------------------------------------------------- #


async def test_customers_requires_auth(client: AsyncClient) -> None:
    """No Authorization header → 401."""
    resp = await client.get(CUSTOMERS_URL)
    assert resp.status_code == 401


async def test_customers_rejects_non_admin_token(client: AsyncClient) -> None:
    """Valid JWT but type != 'admin' → 403."""
    token = jwt.encode(
        {"sub": "1", "type": "user", "exp": int(time.time()) + 3600},
        settings.secret_key.get_secret_value(),
        algorithm=settings.algorithm,
    )
    resp = await client.get(CUSTOMERS_URL, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
