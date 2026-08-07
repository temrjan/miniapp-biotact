"""The periodic sweep: abandoned orders of EVERY payment kind get reclaimed.

Card-only abandoned orders had no reclaimer at all. Multicard sends no webhook for an abandoned
invoice, and the reactive cleanup is gated behind ``if points_to_apply > 0`` in the order-create
route — so a customer who walked away from a pure card payment left an order sitting in PENDING
forever, reading as «деньги вот-вот спишутся». 68 such orders had accumulated on production.

Why a separate function rather than relaxing ``expire_stale_partials``: that one also backs the
«вернуть баллы» button with ``max_age=None``. Relaxing its filter would cancel a pure-card order
the customer is paying AT THAT MOMENT. The two triggers keep their own safety envelopes — and the
test below pins exactly that.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.orders.models import Order, OrderStatus
from src.orders.service import expire_stale_orders, expire_stale_partials
from src.payments.service import multicard_client
from src.users.models import User

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession):
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def deleted_invoices(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Multicard annuls every invoice we ask about — the confirmed-deletion path."""
    deleted: list[str] = []

    async def _fake_delete(uuid: str) -> bool:
        deleted.append(uuid)
        return True

    monkeypatch.setattr(multicard_client, "delete_invoice", _fake_delete)
    return deleted


async def _seed(
    sm: async_sessionmaker[AsyncSession],
    *,
    telegram_id: int,
    age: timedelta,
    points_applied: int = 0,
    payment_status: str = "pending",
    order_status: OrderStatus = OrderStatus.PENDING,
) -> int:
    """Commit one aged order. ``points_applied=0`` is the card-only case nobody used to reclaim."""
    async with sm() as session, session.begin():
        user = User(telegram_id=telegram_id, first_name="Buyer")
        session.add(user)
        await session.flush()
        order = Order(
            order_number=f"ORD-SWEEP-{telegram_id}",
            user_id=user.id,
            subtotal=Decimal("50000.00"),
            delivery_fee=Decimal("0"),
            total=Decimal("50000.00"),
            points_applied=points_applied,
            payment_uuid=f"uuid-{telegram_id}",
            payment_status=payment_status,
            status=order_status,
            created_at=datetime.now(UTC) - age,
        )
        session.add(order)
        await session.flush()
        return order.id


async def _committed(sm: async_sessionmaker[AsyncSession], order_id: int) -> Order:
    async with sm() as session:
        return (await session.execute(select(Order).where(Order.id == order_id))).scalar_one()


async def test_sweep_reclaims_an_abandoned_card_only_order(
    maker: async_sessionmaker[AsyncSession], deleted_invoices: list[str]
) -> None:
    # Тот самый случай, ради которого всё делается: баллов нет, значит прежняя уборка его не видела.
    order_id = await _seed(maker, telegram_id=90001, age=timedelta(hours=3), points_applied=0)

    async with maker() as session:
        outcome = await expire_stale_orders(session)
        await session.commit()

    assert outcome.released_orders == 1
    assert deleted_invoices == ["uuid-90001"]  # счёт аннулирован ДО отмены заказа
    order = await _committed(maker, order_id)
    assert order.status == OrderStatus.CANCELLED
    assert order.payment_status == "failed"


async def test_sweep_leaves_a_fresh_order_alone(
    maker: async_sessionmaker[AsyncSession], deleted_invoices: list[str]
) -> None:
    # Человек платит прямо сейчас. Отменить его заказ — забрать оплату из-под рук.
    order_id = await _seed(maker, telegram_id=90002, age=timedelta(minutes=5), points_applied=0)

    async with maker() as session:
        outcome = await expire_stale_orders(session)
        await session.commit()

    assert outcome.released_orders == 0
    assert deleted_invoices == []
    assert (await _committed(maker, order_id)).status == OrderStatus.PENDING


async def test_sweep_never_touches_a_paid_order(
    maker: async_sessionmaker[AsyncSession], deleted_invoices: list[str]
) -> None:
    order_id = await _seed(
        maker,
        telegram_id=90003,
        age=timedelta(days=30),
        payment_status="paid",
        order_status=OrderStatus.CONFIRMED,
    )

    async with maker() as session:
        outcome = await expire_stale_orders(session)
        await session.commit()

    assert outcome.released_orders == 0
    assert deleted_invoices == []
    assert (await _committed(maker, order_id)).status == OrderStatus.CONFIRMED


async def test_sweep_keeps_an_order_whose_invoice_refuses_deletion(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ удаления = «возможно оплачен». Отменять нельзя — это защита от третьего пути прогара."""

    async def _refuse(uuid: str) -> bool:
        return False

    monkeypatch.setattr(multicard_client, "delete_invoice", _refuse)
    order_id = await _seed(maker, telegram_id=90004, age=timedelta(hours=5))

    async with maker() as session:
        outcome = await expire_stale_orders(session)
        await session.commit()

    assert outcome.released_orders == 0
    assert outcome.blocked_orders == 1
    order = await _committed(maker, order_id)
    assert order.status == OrderStatus.PENDING
    assert order.payment_status == "pending"


async def test_button_path_still_ignores_card_only_orders(
    maker: async_sessionmaker[AsyncSession], deleted_invoices: list[str]
) -> None:
    """The «вернуть баллы» button sweeps with ``max_age=None`` — no age bar at all."""
    # Сними фильтр внутри `expire_stale_partials` — и эта кнопка отменит карточный заказ, который
    # человек оплачивает прямо сейчас. Тест пришпиливает границу между двумя уборками: у них
    # разные триггеры и разные предохранители.
    order_id = await _seed(maker, telegram_id=90005, age=timedelta(minutes=1), points_applied=0)

    async with maker() as session:
        user_id = (await _committed(maker, order_id)).user_id
        outcome = await expire_stale_partials(session, user_id=user_id, max_age=None)
        await session.commit()

    assert outcome.released_orders == 0
    assert deleted_invoices == []
    assert (await _committed(maker, order_id)).status == OrderStatus.PENDING


async def test_sweep_paces_its_calls_to_the_payment_provider(
    maker: async_sessionmaker[AsyncSession],
    deleted_invoices: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Between orders the sweep waits: each one is a separate Multicard call."""
    # Не осторожность, а замер: 07.08.2026 уборка 68 заказов вместе с диагностикой получила
    # `429 Too Many Requests` на `/auth`. Отказ безопасен (заказ просто не отменится и попадёт в
    # следующий прогон), но залп тратит вызовы впустую.
    pauses: list[float] = []

    async def _record(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr("src.orders.service.asyncio.sleep", _record)
    await _seed(maker, telegram_id=90006, age=timedelta(hours=2))
    await _seed(maker, telegram_id=90007, age=timedelta(hours=2))

    async with maker() as session:
        outcome = await expire_stale_orders(session)
        await session.commit()

    assert outcome.released_orders == 2
    assert len(pauses) == 1  # пауза между вызовами, но не перед первым


async def test_expired_invoice_is_cancelled_without_calling_multicard(
    maker: async_sessionmaker[AsyncSession], deleted_invoices: list[str]
) -> None:
    """An invoice past its 24h TTL is already dead — the sweep must not call the provider at all."""
    # Ответ на вопрос Капитана: «оплачен ли» мы знаем из своих данных — успешная оплата приходит
    # колбэком и переводит заказ в paid, а такой в выборку не попадает. Звонок нужен только чтобы
    # ПОГАСИТЬ живой счёт; у просроченного гасить нечего. Отсюда ноль вызовов на старом хвосте —
    # именно это убирает причину 429, а не пауза.
    order_id = await _seed(maker, telegram_id=90008, age=timedelta(days=3))

    async with maker() as session:
        outcome = await expire_stale_orders(session)
        await session.commit()

    assert outcome.released_orders == 1
    assert deleted_invoices == []  # ни одного обращения к Multicard
    order = await _committed(maker, order_id)
    assert order.status == OrderStatus.CANCELLED
    assert order.payment_status == "failed"


async def test_live_invoice_is_still_closed_before_cancelling(
    maker: async_sessionmaker[AsyncSession], deleted_invoices: list[str]
) -> None:
    """Inside the TTL the invoice is still payable, so it must be annulled BEFORE the cancel."""
    # Иначе человек заплатит по старой ссылке, а поздний success не применится: он разрешён только
    # из unpaid/pending, а мы поставим failed. Деньги спишутся, заказ останется отменённым.
    await _seed(maker, telegram_id=90009, age=timedelta(hours=5))

    async with maker() as session:
        outcome = await expire_stale_orders(session)
        await session.commit()

    assert outcome.released_orders == 1
    assert deleted_invoices == ["uuid-90009"]
