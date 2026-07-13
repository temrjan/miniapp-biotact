"""Tests for the server-priced checkout (PR-6.0) — the first tests of the money path.

Before this PR the client declared ``product_price``/``subtotal``/``total`` and the server wrote
them straight into the DB and into the Multicard invoice, unauthenticated. These tests pin the
three properties that closed that hole: the catalogue is the only source of money, identity is
required, and an order belongs to exactly one person.

Seeding commits through a second sessionmaker on the same test DB (READ COMMITTED visibility),
mirroring the loyalty suites; auth headers reuse ``_make_init_data`` from conftest.
"""

from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.orders.models import Order
from src.products.models import Product
from src.users.models import User
from tests.conftest import _make_init_data

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"
BOT_TOKEN = "test-bot-token-123"

ORDERS_URL = "/api/v1/orders"
PAYMENTS_URL = "/api/v1/payments/create"

CATALOGUE_PRICE = Decimal("69000.00")


def _headers(telegram_id: int, first_name: str = "Buyer") -> dict[str, str]:
    """Valid Telegram initData headers for ``telegram_id`` (the mini-app's only auth)."""
    return {"X-Init-Data": _make_init_data(BOT_TOKEN, {"id": telegram_id, "first_name": first_name})}


@pytest.fixture(autouse=True)
def _pin_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """initData is HMAC-signed with the bot token — pin it so ``_make_init_data`` validates.

    Same approach as the loyalty suites; the no-auth cases are unaffected (they send no header,
    and ``get_current_user`` rejects that before it ever looks at the token).
    """
    monkeypatch.setattr(settings, "telegram_bot_token", BOT_TOKEN)


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> object:
    """A sessionmaker on the same test DB ``test_db`` uses — for committed seeding/asserts."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _seed_product(
    sm: async_sessionmaker[AsyncSession],
    *,
    slug: str,
    price: Decimal = CATALOGUE_PRICE,
    in_stock: bool = True,
) -> int:
    """Commit one catalogue product; return its id."""
    async with sm() as session, session.begin():
        product = Product(
            name=f"Product {slug}",
            slug=slug,
            price=price,
            is_in_stock=in_stock,
        )
        session.add(product)
        await session.flush()
        return product.id


async def _seed_user(sm: async_sessionmaker[AsyncSession], telegram_id: int) -> int:
    """Commit a user the way ``get_or_create_user`` would; return its id."""
    async with sm() as session, session.begin():
        user = User(telegram_id=telegram_id, first_name="Seeded")
        session.add(user)
        await session.flush()
        return user.id


async def _seed_order(
    sm: async_sessionmaker[AsyncSession],
    *,
    user_id: int,
    total: Decimal = CATALOGUE_PRICE,
) -> int:
    """Commit an order owned by ``user_id``; return its id."""
    async with sm() as session, session.begin():
        order = Order(
            order_number=f"ORD-TEST-{user_id}-{int(total)}",
            user_id=user_id,
            subtotal=total,
            delivery_fee=Decimal("0"),
            total=total,
        )
        session.add(order)
        await session.flush()
        return order.id


# --------------------------------------------------------------------------- #
# the hole itself: client-declared money must never reach the DB
# --------------------------------------------------------------------------- #


async def test_tampered_item_price_is_ignored(
    client: AsyncClient, maker, test_db: AsyncSession
) -> None:
    """A client claiming the product costs 1 sum gets billed the CATALOGUE price."""
    product_id = await _seed_product(maker, slug="tamper-price")

    resp = await client.post(
        ORDERS_URL,
        json={
            "items": [
                {
                    "product_id": product_id,
                    "quantity": 1,
                    # The old client shape — the attack payload. Must be dropped, not trusted.
                    "product_name": "Free lunch",
                    "product_price": 1,
                    "total": 1,
                },
            ],
            "subtotal": 1,
            "total": 1,
            "delivery_fee": 0,
        },
        headers=_headers(70001),
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert Decimal(body["total"]) == CATALOGUE_PRICE  # not 1
    assert Decimal(body["subtotal"]) == CATALOGUE_PRICE
    item = body["items"][0]
    assert Decimal(item["product_price"]) == CATALOGUE_PRICE
    assert Decimal(item["total"]) == CATALOGUE_PRICE
    assert item["product_name"] == "Product tamper-price"  # catalogue name, not the client's


async def test_old_client_payload_still_works(client: AsyncClient, maker) -> None:
    """The mini-app build live today (product_id + prices) keeps working — deploy-window safety."""
    product_id = await _seed_product(maker, slug="old-client")

    resp = await client.post(
        ORDERS_URL,
        json={
            "items": [
                {
                    "product_id": product_id,
                    "product_name": "Whatever",
                    "product_price": 69000,
                    "quantity": 2,
                    "total": 138000,
                },
            ],
            "subtotal": 138000,
            "delivery_fee": 0,
            "total": 138000,
            "delivery_phone": "+998900000000",
        },
        headers=_headers(70002),
    )

    assert resp.status_code == 201, resp.text
    assert Decimal(resp.json()["total"]) == CATALOGUE_PRICE * 2


async def test_multi_line_totals_are_computed_by_the_server(
    client: AsyncClient, maker
) -> None:
    """Two products with different quantities: subtotal is the sum of the catalogue lines."""
    cheap = await _seed_product(maker, slug="cheap", price=Decimal("15000.00"))
    dear = await _seed_product(maker, slug="dear", price=Decimal("120000.00"))

    resp = await client.post(
        ORDERS_URL,
        json={
            "items": [
                {"product_id": cheap, "quantity": 3},  # 45 000
                {"product_id": dear, "quantity": 2},  # 240 000
            ],
        },
        headers=_headers(70003),
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert Decimal(body["subtotal"]) == Decimal("285000.00")
    assert Decimal(body["delivery_fee"]) == Decimal("0")
    assert Decimal(body["total"]) == Decimal("285000.00")


# --------------------------------------------------------------------------- #
# line validation
# --------------------------------------------------------------------------- #


async def test_unknown_product_is_rejected(client: AsyncClient, maker) -> None:
    """An order for a product that does not exist → 422, no order is created."""
    await _seed_product(maker, slug="real")

    resp = await client.post(
        ORDERS_URL,
        json={"items": [{"product_id": 999999, "quantity": 1}]},
        headers=_headers(70004),
    )

    assert resp.status_code == 422
    assert "999999" in resp.text


async def test_out_of_stock_product_is_rejected(client: AsyncClient, maker) -> None:
    """A product flagged out of stock cannot be ordered → 422."""
    product_id = await _seed_product(maker, slug="sold-out", in_stock=False)

    resp = await client.post(
        ORDERS_URL,
        json={"items": [{"product_id": product_id, "quantity": 1}]},
        headers=_headers(70005),
    )

    assert resp.status_code == 422


async def test_empty_items_rejected(client: AsyncClient) -> None:
    """An order with no lines → 422 (min_length=1), never a zero-sum order."""
    resp = await client.post(ORDERS_URL, json={"items": []}, headers=_headers(70006))
    assert resp.status_code == 422


async def test_quantity_bounds_reject_invalid(client: AsyncClient, maker) -> None:
    """quantity 0 and 100 are refused — the cap guards against an absurd invoice."""
    product_id = await _seed_product(maker, slug="bounds-bad")

    for quantity in (0, 100):
        resp = await client.post(
            ORDERS_URL,
            json={"items": [{"product_id": product_id, "quantity": quantity}]},
            headers=_headers(70007),
        )
        assert resp.status_code == 422, f"quantity={quantity} must be refused"


async def test_quantity_bounds_accept_valid_edges(client: AsyncClient, maker) -> None:
    """quantity 1 and 99 are accepted — pins the boundary so it cannot silently drift down.

    (Gate-1 MINOR-3: testing only the invalid side would let ``le=99`` become ``le=9`` unnoticed.)
    """
    product_id = await _seed_product(maker, slug="bounds-good")

    for quantity in (1, 99):
        resp = await client.post(
            ORDERS_URL,
            json={"items": [{"product_id": product_id, "quantity": quantity}]},
            headers=_headers(70008),
        )
        assert resp.status_code == 201, f"quantity={quantity} must be accepted: {resp.text}"
        assert Decimal(resp.json()["total"]) == CATALOGUE_PRICE * quantity


# --------------------------------------------------------------------------- #
# identity: no anonymous orders, no reading someone else's
# --------------------------------------------------------------------------- #


async def test_create_order_requires_auth(client: AsyncClient, maker) -> None:
    """No initData → 401. It used to be 201, writing the order onto user_id=1."""
    product_id = await _seed_product(maker, slug="no-auth")

    resp = await client.post(
        ORDERS_URL,
        json={"items": [{"product_id": product_id, "quantity": 1}]},
    )

    assert resp.status_code == 401


async def test_list_orders_requires_auth(client: AsyncClient) -> None:
    """No initData → 401. It used to return user_id=1's orders to anyone."""
    resp = await client.get(ORDERS_URL)
    assert resp.status_code == 401


async def test_get_order_requires_auth(client: AsyncClient, maker) -> None:
    """The IDOR: an anonymous caller must NOT read an order (address/phone/name/contents)."""
    owner_id = await _seed_user(maker, telegram_id=70010)
    order_id = await _seed_order(maker, user_id=owner_id)

    resp = await client.get(f"{ORDERS_URL}/{order_id}")

    assert resp.status_code == 401


async def test_get_someone_elses_order_is_forbidden(client: AsyncClient, maker) -> None:
    """A valid user reading a stranger's order → 403."""
    owner_id = await _seed_user(maker, telegram_id=70011)
    order_id = await _seed_order(maker, user_id=owner_id)

    resp = await client.get(f"{ORDERS_URL}/{order_id}", headers=_headers(70012, "Stranger"))

    assert resp.status_code == 403


async def test_owner_reads_own_order(client: AsyncClient, maker) -> None:
    """The owner still reads their own order — the guard closes the hole, not the feature."""
    owner_id = await _seed_user(maker, telegram_id=70013)
    order_id = await _seed_order(maker, user_id=owner_id)

    resp = await client.get(f"{ORDERS_URL}/{order_id}", headers=_headers(70013))

    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == order_id


# --------------------------------------------------------------------------- #
# payments: same defect class, explicit tests (Gate-1 MINOR-1)
# --------------------------------------------------------------------------- #


async def test_create_payment_requires_auth(client: AsyncClient) -> None:
    """No initData → 401. The auth dependency runs before the order lookup, so any id will do."""
    resp = await client.post(PAYMENTS_URL, json={"order_id": 1, "return_url": "https://x.uz"})
    assert resp.status_code == 401


async def test_create_payment_for_someone_elses_order_is_forbidden(
    client: AsyncClient, maker
) -> None:
    """Invoicing a stranger's order → 403 (it also fired the admin Telegram notification)."""
    owner_id = await _seed_user(maker, telegram_id=70014)
    order_id = await _seed_order(maker, user_id=owner_id)

    resp = await client.post(
        PAYMENTS_URL,
        json={"order_id": order_id, "return_url": "https://x.uz"},
        headers=_headers(70015, "Stranger"),
    )

    assert resp.status_code == 403


async def test_create_payment_rejects_zero_total(client: AsyncClient, maker) -> None:
    """A zero-total order is never sent to Multicard (a zero invoice is undocumented).

    The 0 is seeded straight into the DB: the admin product schema validates ``price > 0``, so
    this guard is defence in depth against bad catalogue data, not a reachable client path.
    """
    owner_id = await _seed_user(maker, telegram_id=70016)
    order_id = await _seed_order(maker, user_id=owner_id, total=Decimal("0"))

    resp = await client.post(
        PAYMENTS_URL,
        json={"order_id": order_id, "return_url": "https://x.uz"},
        headers=_headers(70016),
    )

    assert resp.status_code == 400
