"""Отметка завершения заказа пишется во времени UTC, не наивном (PR-6.5-B).

`completed_at` — колонка `timestamptz` (`orders/models.py:96`), но писалась наивным временем.
PostgreSQL на проде живёт при `TimeZone=Europe/Berlin` и принимает голое значение за берлинское:
отметка уезжала на -2ч летом и на -1ч зимой (сдвиг непостоянный из-за перехода на зимнее время,
поэтому историю нельзя выправить одним `UPDATE`).

Путей записи два, и ломались они по-разному:
* `orders/service.py` — наивный `datetime.utcnow()`;
* `orders/admin_router.py` — голый `datetime.now()`, то есть ЛОКАЛЬНОЕ время машины приложения,
  что вдобавок зависит от настроек сервера, не только от PG.

Тесты ловят НАИВНОСТЬ, но не величину: сломанные варианты тоже возвращают «примерно сейчас», и
проверка на близость к текущему моменту дефект бы пропустила.
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
    """Sessionmaker на той же тест-БД — для посева и чтения закоммиченного состояния."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def _seed_order(
    sm: async_sessionmaker[AsyncSession], *, number: str, telegram_id: int
) -> int:
    """Заказ требует владельца: `orders.user_id` — внешний ключ на `users`."""
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
    test_db: AsyncSession, maker: async_sessionmaker[AsyncSession]
) -> None:
    """Сервисный путь: перевод в «доставлен» ставит `completed_at` во времени UTC."""
    order_id = await _seed_order(maker, number="ORD-DONE-SVC", telegram_id=80400)
    before = datetime.now(UTC)

    async with maker() as session, session.begin():
        updated = await update_order_status(session, order_id, "delivered")
        assert updated is not None

    order = await _read_order(maker, order_id)
    assert order.status == OrderStatus.DELIVERED
    assert order.completed_at is not None
    assert order.completed_at.tzinfo is not None, "наивное время: PG примет его за берлинское"
    assert timedelta(0) <= order.completed_at - before < timedelta(minutes=5)


async def test_admin_route_marks_delivered_with_timezone_aware_completed_at(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], admin_headers: dict[str, str]
) -> None:
    """Админский путь: он писал ЛОКАЛЬНОЕ время машины — худший из двух вариантов."""
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
    assert order.completed_at.tzinfo is not None, "наивное время: PG примет его за берлинское"
    assert timedelta(0) <= order.completed_at - before < timedelta(minutes=5)
