"""``completed_at`` is stamped in UTC, not naively (PR-6.5-B).

``completed_at`` is a ``timestamptz`` column (``orders/models.py:96``) that used to be written with
a naive value. The driver treats a naive ``datetime`` as local time of the application PROCESS, so
the stamp drifts by the server's offset — on prod that is -2h in summer and -1h in winter (the shift
is not constant across DST, which is why history cannot be fixed with a single ``UPDATE``). The
PostgreSQL session timezone does not affect this — measured.

There were two write paths, broken differently:
* ``orders/service.py`` — naive ``datetime.utcnow()``;
* ``orders/admin_router.py`` — bare ``datetime.now()``, i.e. the machine's LOCAL time.

The defect only reproduces under the application server's timezone, which ``prod_timezone`` sets:
under ``TZ=UTC`` — exactly how CI runs — both writes land identically.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.orders.models import Order, OrderStatus
from src.orders.service import update_order_status
from src.users.models import User

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"
ADMIN_ORDERS_URL = "/api/v1/admin/orders"


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> object:
    """Sessionmaker on the same test DB — for seeding and reading committed state."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _seed_order(
    sm: async_sessionmaker[AsyncSession], *, number: str, telegram_id: int
) -> int:
    """An order needs an owner: ``orders.user_id`` is a foreign key into ``users``."""
    async with sm() as session, session.begin():
        user = User(telegram_id=telegram_id, first_name="Seed")
        session.add(user)
        await session.flush()
        order = Order(
            order_number=number,
            user_id=user.id,
            subtotal=Decimal("50000.00"),
            delivery_fee=Decimal("0"),
            total=Decimal("50000.00"),
            payment_status="paid",
            status=OrderStatus.SHIPPED,
        )
        session.add(order)
        await session.flush()
        return order.id


async def _read_order(sm: async_sessionmaker[AsyncSession], order_id: int) -> Order:
    async with sm() as session:
        order = await session.get(Order, order_id)
        assert order is not None
        return order


async def test_service_marks_delivered_with_timezone_aware_completed_at(
    test_db: AsyncSession, maker: async_sessionmaker[AsyncSession], prod_timezone: None
) -> None:
    """Service path: marking an order delivered stamps ``completed_at`` without drift."""
    order_id = await _seed_order(maker, number="ORD-DONE-SVC", telegram_id=80400)
    before = datetime.now(UTC)

    async with maker() as session, session.begin():
        updated = await update_order_status(session, order_id, "delivered")
        assert updated is not None

    order = await _read_order(maker, order_id)
    assert order.status == OrderStatus.DELIVERED
    assert order.completed_at is not None
    assert timedelta(0) <= order.completed_at - before < timedelta(minutes=5), (
        f"отметка уехала: completed_at={order.completed_at}, момент операции={before}"
    )


async def test_admin_route_marks_delivered_with_timezone_aware_completed_at(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    admin_headers: dict[str, str],
    prod_timezone: None,
) -> None:
    """Admin path: this one wrote the machine's LOCAL time — the worse of the two bugs."""
    order_id = await _seed_order(maker, number="ORD-DONE-ADMIN", telegram_id=80401)
    before = datetime.now(UTC)

    resp = await client.patch(
        f"{ADMIN_ORDERS_URL}/{order_id}/status",
        json={"status": "delivered"},
        headers=admin_headers,
    )

    assert resp.status_code == 200, resp.text
    order = await _read_order(maker, order_id)
    assert order.completed_at is not None
    assert timedelta(0) <= order.completed_at - before < timedelta(minutes=5), (
        f"отметка уехала: completed_at={order.completed_at}, момент операции={before}"
    )
