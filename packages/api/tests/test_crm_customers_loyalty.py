"""Tests for the Biotact Club enrichment of the admin CRM customers endpoints (PR-2).

First tests of the CRM router at all. Seeding commits through a second sessionmaker on the
same test DB (READ COMMITTED visibility), mirroring test_loyalty_summary.py; the admin JWT
comes from the shared ``admin_headers`` conftest fixture (sub=1 → a committed admin user).
Also covers ``purchased_products``: the regression guard for the explicit OrderItem join
(the pre-fix relationship select produced a cartesian product and broke the endpoint).
"""

import time
from decimal import Decimal

import pytest_asyncio
from httpx import AsyncClient
from jose import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.loyalty.models import CustomerProfile, Gender, LedgerReason, LoyaltyStatus, PointLedger
from src.orders.models import Order, OrderItem, OrderStatus
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


# --------------------------------------------------------------------------- #
# no-survey filter — members who earned points and never filled the survey in
# --------------------------------------------------------------------------- #


async def _seed_reminder_cohort(sm: async_sessionmaker[AsyncSession]) -> None:
    """One member per branch the filter has to decide, plus a plain user that must not leak.

    Both survey columns are seeded alone as well as together: a filter written with AND where the
    rule needs OR would still answer correctly for the empty and the complete profile, and only a
    half-filled one tells the two apart.
    """
    await _seed_admin(sm)
    # TWO positive accruals on purpose. The condition asks whether a credit ever happened, so one
    # member has to carry more than one: written as a JOIN instead of EXISTS — the shape used two
    # lines above for club=qr, and therefore the likely rewrite — this member would be counted once
    # per ledger row. `total` would inflate while `result.unique()` collapsed the duplicates AFTER
    # LIMIT, serving a short page under an honest-looking count. With a single row each, no test
    # could tell the two formulations apart.
    await _seed_customer(sm, 80201, "EarnedNoSurvey", member=True, source="qr", ledger=(500, 300))
    await _seed_customer(
        sm,
        80202,
        "EarnedWithSurvey",
        member=True,
        source="qr",
        ledger=(500,),
        gender=Gender.MALE,
        city="Ташкент",
    )
    await _seed_customer(sm, 80203, "NoPointsNoSurvey", member=True, source="qr")
    await _seed_customer(
        sm, 80204, "EarnedCityOnly", member=True, source="qr", ledger=(300,), city="Ташкент"
    )
    await _seed_customer(
        sm, 80205, "EarnedGenderOnly", member=True, source="qr", ledger=(300,), gender=Gender.MALE
    )
    await _seed_customer(sm, 80206, "PlainShopper")


async def test_customers_filter_no_survey_lists_earners_with_an_unfinished_survey(
    client: AsyncClient, maker, admin_headers: dict[str, str]
) -> None:
    """club=no_survey → earned points AND survey not complete. Both halves are load-bearing.

    A half-filled profile counts as unfinished: the survey is complete only when gender AND city
    are both there (``has_survey``), so either one missing is a reason to remind.
    """
    await _seed_reminder_cohort(maker)

    resp = await client.get(CUSTOMERS_URL, params={"club": "no_survey"}, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # A LIST, not a set: a set would swallow the very duplication the two-accrual member is seeded
    # to expose, and the page would look correct while carrying the same person twice.
    assert sorted(item["telegram_id"] for item in body["items"]) == [80201, 80204, 80205]
    assert body["total"] == 3


async def test_customers_filter_no_survey_keeps_a_member_who_spent_every_point(
    client: AsyncClient, maker, admin_headers: dict[str, str]
) -> None:
    """Points were EVER credited — spending them again does not undo that.

    The rule is the Captain's, ratified 2026-08-10 against the opposite reading proposed at Gate-1
    ("still holds points"): the survey is still unfinished, so the reminder is still due. Seeded
    through a REDEEM row that cancels the earn exactly, which is the case the two readings disagree
    about — a balance of zero over a ledger that once carried a credit.
    """
    await _seed_admin(maker)
    await _seed_customer(maker, 80211, "SpentEverything", member=True, ledger=(500, -500))

    resp = await client.get(CUSTOMERS_URL, params={"club": "no_survey"}, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert [item["telegram_id"] for item in resp.json()["items"]] == [80211]


async def test_customers_filter_no_survey_excludes_an_accrual_that_credited_nothing(
    client: AsyncClient, maker, admin_headers: dict[str, str]
) -> None:
    """A ledger row of zero is an activation that credited nothing — and nothing is not "credited".

    Reachable, not hypothetical: ``compute_cashback`` is exact Decimal math rounded HALF_UP, so a
    small enough ``unit_price`` yields 0 (1 сум at the 250 bps welcome rate → 0.025 → 0), and the
    earn row is written unconditionally either way. Without this the condition could quietly relax
    to "has any ledger row" and start reminding people who were never credited a point.
    """
    await _seed_admin(maker)
    await _seed_customer(maker, 80213, "AccruedZero", member=True, ledger=(0,))

    resp = await client.get(CUSTOMERS_URL, params={"club": "no_survey"}, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []


async def test_customers_filter_no_survey_still_excludes_a_member_who_never_earned(
    client: AsyncClient, maker, admin_headers: dict[str, str]
) -> None:
    """The other half of the same rule: never credited → not on the list, however empty the survey.

    Without this the filter could quietly become "every member without a survey" — the population
    it is meant to be a small subset of.
    """
    await _seed_admin(maker)
    await _seed_customer(maker, 80212, "NeverEarned", member=True)

    resp = await client.get(CUSTOMERS_URL, params={"club": "no_survey"}, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"] == []


async def test_customers_filter_no_survey_pagination_math(
    client: AsyncClient, maker, admin_headers: dict[str, str]
) -> None:
    """13 matching members → page 2 holds exactly 1 and total counts the FILTERED set.

    The count query has to carry the same conditions as the data query; if it does not, the page
    is right while ``total``/``pages`` describe a different, larger population.
    """
    await _seed_admin(maker)
    await _seed_customer(maker, 80250, "PlainAround")
    await _seed_customer(
        maker, 80251, "SurveyDone", member=True, ledger=(500,), gender=Gender.MALE, city="Ташкент"
    )
    for i in range(13):
        await _seed_customer(maker, 80300 + i, f"Owed{i:02d}", member=True, ledger=(100,))

    resp = await client.get(
        CUSTOMERS_URL,
        params={"club": "no_survey", "page": 2, "size": 12},
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 13
    assert body["pages"] == 2
    assert len(body["items"]) == 1


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
# purchased_products — explicit OrderItem join, not a cartesian product
# --------------------------------------------------------------------------- #


async def _seed_order(
    sm: async_sessionmaker[AsyncSession],
    user_id: int,
    status: OrderStatus,
    items: list[str],
) -> None:
    """Commit an order with one item row per name; ``product_id`` is nullable — names only."""
    async with sm() as s, s.begin():
        order = Order(
            order_number=f"CRM-{user_id}-{status.value}-{len(items)}",
            user_id=user_id,
            status=status,
            subtotal=Decimal("0"),
            total=Decimal("0"),
        )
        s.add(order)
        await s.flush()
        for name in items:
            s.add(
                OrderItem(
                    order_id=order.id,
                    product_name=name,
                    product_price=Decimal("0"),
                    quantity=1,
                    total=Decimal("0"),
                )
            )


async def _seed_purchase_cohort(sm: async_sessionmaker[AsyncSession]) -> None:
    """A: delivered order with 2 items + a cancelled order with an item; B: delivered with 1; C: none."""
    await _seed_admin(sm)
    buyer_a = await _seed_customer(sm, 80401, "BuyerA")
    buyer_b = await _seed_customer(sm, 80402, "BuyerB")
    await _seed_customer(sm, 80403, "NoOrders")
    await _seed_order(sm, buyer_a, OrderStatus.DELIVERED, ["Витамин C", "Омега-3"])
    await _seed_order(sm, buyer_a, OrderStatus.CANCELLED, ["Отменённый"])
    await _seed_order(sm, buyer_b, OrderStatus.DELIVERED, ["Магний"])


async def test_customers_purchased_products_come_from_their_own_completed_orders(
    client: AsyncClient, maker, admin_headers: dict[str, str]
) -> None:
    """purchased_products = names from the customer's own delivered/shipped/processing orders only.

    Exact-set assertions are load-bearing in both directions: the cancelled order's item must NOT
    leak in (filter), and each buyer must see ONLY their own names (no cross-user contamination —
    the failure mode of the pre-fix cartesian product, which mixed rows across the whole page).
    """
    await _seed_purchase_cohort(maker)

    resp = await client.get(CUSTOMERS_URL, headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert set(_by_telegram_id(body, 80401)["purchased_products"]) == {"Витамин C", "Омега-3"}
    assert set(_by_telegram_id(body, 80402)["purchased_products"]) == {"Магний"}
    assert _by_telegram_id(body, 80403)["purchased_products"] == []


async def test_customer_single_purchased_products_match_the_list_endpoint(
    client: AsyncClient, maker, admin_headers: dict[str, str]
) -> None:
    """The single-customer endpoint resolves the same names as the list — through its own query."""
    await _seed_purchase_cohort(maker)

    resp = await client.get(CUSTOMER_URL.format(80401), headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert set(resp.json()["purchased_products"]) == {"Витамин C", "Омега-3"}

    resp = await client.get(CUSTOMER_URL.format(80403), headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["purchased_products"] == []


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
