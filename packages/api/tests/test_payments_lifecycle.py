"""Tests for the payment lifecycle — idempotency, amount reconciliation, webhooks (PR-6.1).

This is the money path's gate against duplicate processing: Multicard repeats the success
callback on any 2xx timeout and retries webhooks up to 5x until a 2xx. Before this PR every
repeat re-wrote the order and re-notified admins; with REDEEM (Stage 6) a repeat would move
points twice. These tests pin: a duplicate (uuid, status) is a no-op, a wrong amount is refused,
failure/revert webhooks cancel the order, and two concurrent callbacks apply exactly once.

Requests run through a ``get_db`` override that mirrors production (commit on return, rollback on
raise) — the conftest override does neither, which would make "the order was NOT re-written"
untestable. Committed truth is read through a second sessionmaker (READ COMMITTED).
"""

import asyncio
import copy
import hashlib
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.database import get_db
from src.employee_referrals.delivery_models import K2Delivery, NoticeDelivery
from src.employee_referrals.paid import PaidUnavailableError
from src.employee_referrals.paid_models import PaidOutbox
from src.loyalty import service as loyalty_service
from src.loyalty.models import CustomerProfile, LedgerReason, PointLedger
from src.main import app
from src.orders.models import Order, OrderItem, OrderStatus
from src.payments import lifecycle as lc
from src.payments import router as payment_router
from src.payments.models import PaymentEvent
from src.payments.service import multicard_client
from src.products.models import Product
from src.users.models import User

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"
SECRET = "test-multicard-secret"

CALLBACK_URL = "/api/v1/payments/callback"
WEBHOOK_URL = "/api/v1/payments/webhook"

ORDER_TOTAL = Decimal("50000.00")
DUE_TIYINS = 5_000_000  # (total - points_applied) * 100, points_applied = 0
STORE_ID = 6


def _md5(store_id: int, invoice_id: str, amount: int) -> str:
    return hashlib.md5(f"{store_id}{invoice_id}{amount}{SECRET}".encode()).hexdigest()


def _sha1(uuid: str, invoice_id: str, amount: int) -> str:
    return hashlib.sha1(f"{uuid}{invoice_id}{amount}{SECRET}".encode()).hexdigest()


def _callback_body(
    *, uuid: str, invoice_id: str, amount: int = DUE_TIYINS, sign: str | None = None
):
    return {
        "store_id": STORE_ID,
        "amount": amount,
        "invoice_id": invoice_id,
        "uuid": uuid,
        "ps": "uzcard",
        "sign": sign if sign is not None else _md5(STORE_ID, invoice_id, amount),
    }


def _webhook_body(
    *, uuid: str, invoice_id: str, status: str, amount: int = DUE_TIYINS, sign: str | None = None
):
    return {
        "uuid": uuid,
        "invoice_id": invoice_id,
        "amount": amount,
        "status": status,
        "sign": sign if sign is not None else _sha1(uuid, invoice_id, amount),
    }


@pytest.fixture(autouse=True)
def _pin_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the Multicard secret so our test signatures validate against the real verifier."""
    monkeypatch.setattr(multicard_client, "secret", SECRET)


@pytest.fixture(autouse=True)
def sent_messages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture Telegram sends instead of hitting the network; assert notify fired (or did not)."""
    captured: list[str] = []

    async def _capture(text: str, parse_mode: str = "HTML") -> bool:
        captured.append(text)
        return True

    monkeypatch.setattr("src.payments.router.telegram_notifier.send_message", _capture)
    return captured


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield sm
    await engine.dispose()


@pytest_asyncio.fixture
async def client(
    maker: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    """Client whose DB dependency commits on success / rolls back on raise — like production."""

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# committed seeding / committed-truth helpers
# --------------------------------------------------------------------------- #


async def _seed_order(
    sm: async_sessionmaker[AsyncSession],
    *,
    order_number: str,
    total: Decimal = ORDER_TOTAL,
    points_applied: int = 0,
    payment_uuid: str | None = None,
    payment_status: str = "unpaid",
    order_status: OrderStatus = OrderStatus.PENDING,
    points_balance: int = 0,
    product_slug: str | None = None,
    orphan_item: bool = False,
    sale_regular_price: Decimal | None = None,
) -> int:
    """Seed an order. ``payment_uuid`` "arms" a Multicard attempt on it (as /payments/create does);
    an event applies only if its uuid matches this active attempt. ``points_balance`` (> 0) also
    gives the order's user a club profile and that many points — needed to prove the Model-1 debit
    that ``commit_redeem`` writes when a PARTIAL order's card leg settles."""
    async with sm() as session, session.begin():
        user = User(telegram_id=hash(order_number) % 1_000_000_000, first_name="Buyer")
        session.add(user)
        await session.flush()
        if points_balance > 0:
            await session.execute(
                pg_insert(CustomerProfile)
                .values(user_id=user.id)
                .on_conflict_do_nothing(index_elements=["user_id"])
            )
            session.add(
                PointLedger(user_id=user.id, amount=points_balance, reason=LedgerReason.CASHBACK)
            )
        order = Order(
            order_number=order_number,
            user_id=user.id,
            subtotal=total,
            delivery_fee=Decimal("0"),
            total=total,
            points_applied=points_applied,
            payment_uuid=payment_uuid,
            payment_status=payment_status,
            status=order_status,
        )
        session.add(order)
        await session.flush()
        if orphan_item:
            # Товар удалён после заказа: `order_items.product_id` — nullable с ON DELETE SET NULL,
            # позиция живёт дальше без товара. Уведомление обязано уйти всё равно.
            session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=None,
                    product_name="Снятый с продажи товар",
                    product_price=total,
                    quantity=1,
                    total=total,
                )
            )
            await session.flush()
        elif product_slug is not None:
            # Позиция с товаром нужна только там, где проверяется СОДЕРЖИМОЕ строки товаров
            # (форма выпуска выводится из slug'а). По умолчанию заказ по-прежнему без позиций —
            # остальные тесты этого файла не меняются.
            product = Product(
                name="BIFOLAK® ACTIVE",
                slug=product_slug,
                price=sale_regular_price if sale_regular_price is not None else total,
                is_sale=sale_regular_price is not None,
                sale_price=total if sale_regular_price is not None else None,
                is_in_stock=True,
            )
            session.add(product)
            await session.flush()
            session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    product_name=product.name,
                    product_price=total,
                    is_sale_at_order=True if sale_regular_price is not None else None,
                    regular_price_at_order=sale_regular_price,
                    quantity=1,
                    total=total,
                )
            )
            await session.flush()
        return order.id


async def _committed_order(sm: async_sessionmaker[AsyncSession], order_id: int) -> Order:
    async with sm() as session:
        return (await session.execute(select(Order).where(Order.id == order_id))).scalar_one()


async def _committed_redeem_rows(sm: async_sessionmaker[AsyncSession]) -> list[PointLedger]:
    async with sm() as session:
        result = await session.execute(
            select(PointLedger).where(PointLedger.reason == LedgerReason.REDEEM.value)
        )
        return list(result.scalars().all())


async def _committed_balance(sm: async_sessionmaker[AsyncSession], user_id: int) -> int:
    async with sm() as session:
        return await loyalty_service.get_balance(session, user_id)


async def _event_count(sm: async_sessionmaker[AsyncSession], uuid: str, status: str) -> int:
    async with sm() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(PaymentEvent)
                    .where(PaymentEvent.payment_uuid == uuid, PaymentEvent.status == status)
                )
            ).scalar_one()
        )


# --------------------------------------------------------------------------- #
# signature verification
# --------------------------------------------------------------------------- #


async def test_callback_bad_md5_signature_is_rejected(client: AsyncClient, maker) -> None:
    await _seed_order(maker, order_number="ORD-SIG-CB")
    resp = await client.post(
        CALLBACK_URL, json=_callback_body(uuid="u-sig", invoice_id="ORD-SIG-CB", sign="deadbeef")
    )
    assert resp.status_code == 400


async def test_webhook_bad_sha1_signature_is_rejected(client: AsyncClient, maker) -> None:
    await _seed_order(maker, order_number="ORD-SIG-WH")
    resp = await client.post(
        WEBHOOK_URL,
        json=_webhook_body(uuid="u-sig", invoice_id="ORD-SIG-WH", status="success", sign="bad"),
    )
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# success callback — happy path + idempotency
# --------------------------------------------------------------------------- #


async def test_success_callback_marks_paid_and_notifies(
    client: AsyncClient, maker, sent_messages: list[str]
) -> None:
    order_id = await _seed_order(
        maker, order_number="ORD-OK", payment_uuid="u-ok", payment_status="pending"
    )

    resp = await client.post(CALLBACK_URL, json=_callback_body(uuid="u-ok", invoice_id="ORD-OK"))

    assert resp.status_code == 200, resp.text
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "paid"
    assert order.status == OrderStatus.CONFIRMED
    assert order.paid_at is not None
    assert order.payment_uuid == "u-ok"
    assert await _event_count(maker, "u-ok", "success") == 1
    assert len(sent_messages) == 1


async def test_duplicate_success_callback_is_noop(
    client: AsyncClient, maker, sent_messages: list[str]
) -> None:
    """The core guarantee: a repeated callback does NOT re-write the order or re-notify admins.

    Mutation: drop ``on_conflict_do_nothing`` in lifecycle.process_payment_event → the second
    call re-applies and re-notifies → this test goes red (and the DB would raise on the dup insert).
    """
    await _seed_order(maker, order_number="ORD-DUP", payment_uuid="u-dup", payment_status="pending")
    body = _callback_body(uuid="u-dup", invoice_id="ORD-DUP")

    first = await client.post(CALLBACK_URL, json=body)
    second = await client.post(CALLBACK_URL, json=body)

    assert first.status_code == 200
    assert second.status_code == 200  # duplicate still acknowledged
    assert await _event_count(maker, "u-dup", "success") == 1  # exactly one event row
    assert len(sent_messages) == 1  # notified once, not twice


async def test_amount_mismatch_is_rejected_and_not_paid(
    client: AsyncClient, maker, sent_messages: list[str]
) -> None:
    """A success whose amount ≠ what the order owes → 400, order untouched, nothing recorded.

    Mutation: drop the amount check in process_payment_event → order flips to paid → red.
    """
    order_id = await _seed_order(
        maker, order_number="ORD-MISMATCH", payment_uuid="u-mm", payment_status="pending"
    )
    body = _callback_body(uuid="u-mm", invoice_id="ORD-MISMATCH", amount=DUE_TIYINS - 100_000)

    resp = await client.post(CALLBACK_URL, json=body)

    assert resp.status_code == 400
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "pending"  # unchanged — raise rolled the whole tx back
    assert order.status == OrderStatus.PENDING
    assert await _event_count(maker, "u-mm", "success") == 0  # nothing persisted (rolled back)
    assert sent_messages == []


async def test_partial_order_paid_via_callback_commits_redeem_and_notifies_paid(
    client: AsyncClient, maker, sent_messages: list[str]
) -> None:
    """A PARTIAL order settled by the card callback COMMITS the reserved debit and is «ОПЛАЧЕНО».

    Model-1 commit-on-paid: the partial only RESERVED its 45 000 points at creation (no ledger row).
    The card collects the 5 000 remainder (amount reconciled against ``total - points_applied``), and
    only now — inside the state-guarded success branch — does ``commit_redeem`` write the real
    -45 000 debit. The admin message keeps the «ОПЛАЧЕНО» header (the card, not points, closed it —
    the ratified header-by-coverage rule) yet records the points spent.
    Mutations: (a) drop the ``commit_redeem`` call in ``process_payment_event`` → no redeem row, the
    balance stays 45 000 → red; (b) drop ``points_applied`` from ``_notify_paid`` → the «Списано
    баллов» line disappears → red.
    """
    order_id = await _seed_order(
        maker,
        order_number="ORD-PARTIAL",
        total=Decimal("50000.00"),
        points_applied=45_000,
        payment_uuid="u-partial",
        payment_status="pending",
        points_balance=45_000,  # reserved at creation; the debit lands now, on settlement
    )
    remainder_tiyins = (50_000 - 45_000) * 100  # what the card actually collects

    resp = await client.post(
        CALLBACK_URL,
        json=_callback_body(uuid="u-partial", invoice_id="ORD-PARTIAL", amount=remainder_tiyins),
    )

    assert resp.status_code == 200, resp.text
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "paid"

    # commit-on-paid: exactly one redeem debit is written now, and the balance drops by it.
    redeem_rows = await _committed_redeem_rows(maker)
    assert len(redeem_rows) == 1
    assert redeem_rows[0].order_id == order_id
    assert redeem_rows[0].amount == -45_000
    assert await _committed_balance(maker, order.user_id) == 0

    assert len(sent_messages) == 1
    message = sent_messages[0]
    assert "ОПЛАЧЕНО" in message
    assert "ОПЛАЧЕНО БАЛЛАМИ" not in message  # the card closed it, not points
    assert "Списано баллов" in message  # but the points spent are still recorded


async def test_paid_notification_carries_the_release_form(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], sent_messages: list[str]
) -> None:
    """The card-paid notice names the release form, so sales know what to put in the parcel.

    Proves the slug travels the whole way — order item → ``items_data`` → formatter — not merely
    that the formatter can render it. Two catalogue pairs share a name and a price and differ only
    in form, so without this the line is ambiguous.
    Mutation: drop ``"slug"`` from ``items_data`` in ``_notify_paid`` → red.
    """
    await _seed_order(
        maker,
        order_number="ORD-FORM",
        payment_uuid="u-form",
        payment_status="pending",
        product_slug="bifolak_active_caps",
    )

    resp = await client.post(
        CALLBACK_URL,
        json=_callback_body(uuid="u-form", invoice_id="ORD-FORM", amount=int(ORDER_TOTAL) * 100),
    )

    assert resp.status_code == 200, resp.text
    assert len(sent_messages) == 1
    assert "BIFOLAK® ACTIVE (капсулы)" in sent_messages[0]


async def test_paid_notification_survives_a_deleted_product(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], sent_messages: list[str]
) -> None:
    """A line whose product was deleted still gets its notification — without a form mark.

    ``order_items.product_id`` is nullable with ON DELETE SET NULL, so the line outlives the
    product. Reading ``item.product.slug`` unguarded raises inside the notify block, which swallows
    exceptions — sales would simply never hear about a paid order, and nothing would be logged as a
    failure of THIS feature. Guards the ``if item.product else None`` in ``_notify_paid``.
    Mutation: drop that guard → red.
    """
    await _seed_order(
        maker,
        order_number="ORD-ORPHAN",
        payment_uuid="u-orphan",
        payment_status="pending",
        orphan_item=True,
    )

    resp = await client.post(
        CALLBACK_URL,
        json=_callback_body(
            uuid="u-orphan", invoice_id="ORD-ORPHAN", amount=int(ORDER_TOTAL) * 100
        ),
    )

    assert resp.status_code == 200, resp.text
    assert len(sent_messages) == 1  # уведомление ушло, несмотря на удалённый товар
    assert "Снятый с продажи товар" in sent_messages[0]
    assert "(капсулы)" not in sent_messages[0]


@pytest.mark.parametrize("change", ["edit", "delete"])
async def test_paid_notice_uses_saved_sale_terms_after_catalog_change(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    sent_messages: list[str],
    change: str,
) -> None:
    order_id = await _seed_order(
        maker,
        order_number="ORD-SALE-SNAPSHOT",
        total=Decimal("313500.00"),
        payment_uuid="u-sale-snapshot",
        payment_status="pending",
        product_slug="snapshot-vest",
        sale_regular_price=Decimal("330000.00"),
    )
    async with maker() as session, session.begin():
        item = (
            await session.scalars(select(OrderItem).where(OrderItem.order_id == order_id))
        ).one()
        assert (item.is_sale_at_order, item.regular_price_at_order, item.product_price) == (
            True,
            Decimal("330000.00"),
            Decimal("313500.00"),
        )
        if change == "delete":
            await session.execute(delete(Product).where(Product.id == item.product_id))
        else:
            product = await session.get(Product, item.product_id)
            product.is_sale = False
            product.price = Decimal("350000.00")
            product.sale_price = Decimal("300000.00")
    if change == "delete":
        async with maker() as session:
            item = (
                await session.scalars(select(OrderItem).where(OrderItem.order_id == order_id))
            ).one()
            assert item.product_id is None

    body = _callback_body(uuid="u-sale-snapshot", invoice_id="ORD-SALE-SNAPSHOT", amount=31_350_000)
    first = await client.post(CALLBACK_URL, json=body)
    second = await client.post(CALLBACK_URL, json=body)
    assert first.status_code == second.status_code == 200
    assert (await _committed_order(maker, order_id)).payment_status == "paid"
    assert len(sent_messages) == 1
    assert (
        "1. BIFOLAK® ACTIVE\nАкция: 330 000 → 313 500 сум\n1 шт. × 313 500 сум"
    ) in sent_messages[0]


async def test_legacy_paid_item_stays_ordinary_after_catalog_becomes_sale(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], sent_messages: list[str]
) -> None:
    order_id = await _seed_order(
        maker,
        order_number="ORD-LEGACY-SNAPSHOT",
        payment_uuid="u-legacy-snapshot",
        payment_status="pending",
        product_slug="legacy-item",
    )
    async with maker() as session, session.begin():
        item = (
            await session.scalars(select(OrderItem).where(OrderItem.order_id == order_id))
        ).one()
        assert (item.is_sale_at_order, item.regular_price_at_order) == (None, None)
        product = await session.get(Product, item.product_id)
        product.is_sale = True
        product.sale_price = Decimal("45000.00")

    response = await client.post(
        CALLBACK_URL,
        json=_callback_body(uuid="u-legacy-snapshot", invoice_id="ORD-LEGACY-SNAPSHOT"),
    )
    assert response.status_code == 200, response.text
    assert len(sent_messages) == 1
    assert "1. BIFOLAK® ACTIVE - 1 шт × 50,000 сум" in sent_messages[0]
    assert "Акция:" not in sent_messages[0]


async def test_callback_unknown_invoice_returns_404(client: AsyncClient, maker) -> None:
    resp = await client.post(CALLBACK_URL, json=_callback_body(uuid="u-x", invoice_id="ORD-NOPE"))
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# webhooks — failure, revert, ignored statuses, retries
# --------------------------------------------------------------------------- #


def _confirm_webhook(monkeypatch: pytest.MonkeyPatch, *, uuid: str, status: str) -> None:
    monkeypatch.setattr(
        multicard_client,
        "get_invoice_status",
        AsyncMock(return_value={"uuid": uuid, "payment": {"status": status}}),
    )


async def test_webhook_error_cancels_order(
    client: AsyncClient, maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    order_id = await _seed_order(
        maker, order_number="ORD-ERR", payment_uuid="u-err", payment_status="pending"
    )
    _confirm_webhook(monkeypatch, uuid="u-err", status="error")
    resp = await client.post(
        WEBHOOK_URL, json=_webhook_body(uuid="u-err", invoice_id="ORD-ERR", status="error")
    )
    assert resp.status_code == 200
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "failed"
    assert order.status == OrderStatus.CANCELLED


async def test_webhook_revert_refunds_order(
    client: AsyncClient, maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    # revert is legal only out of a paid order — arm it as paid by this attempt.
    order_id = await _seed_order(
        maker, order_number="ORD-REV", payment_uuid="u-rev", payment_status="paid"
    )
    _confirm_webhook(monkeypatch, uuid="u-rev", status="revert")
    resp = await client.post(
        WEBHOOK_URL, json=_webhook_body(uuid="u-rev", invoice_id="ORD-REV", status="revert")
    )
    assert resp.status_code == 200
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "refunded"
    assert order.status == OrderStatus.CANCELLED


async def test_duplicate_error_webhook_is_noop(
    client: AsyncClient, maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    order_id = await _seed_order(
        maker, order_number="ORD-ERR2", payment_uuid="u-err2", payment_status="pending"
    )
    _confirm_webhook(monkeypatch, uuid="u-err2", status="error")
    body = _webhook_body(uuid="u-err2", invoice_id="ORD-ERR2", status="error")

    await client.post(WEBHOOK_URL, json=body)
    second = await client.post(WEBHOOK_URL, json=body)

    assert second.status_code == 200
    assert await _event_count(maker, "u-err2", "error") == 1
    order = await _committed_order(maker, order_id)
    assert order.status == OrderStatus.CANCELLED


@pytest.mark.parametrize("status", ["draft", "progress", "hold"])
async def test_webhook_non_terminal_status_leaves_order_untouched(
    client: AsyncClient, maker, status: str
) -> None:
    """draft/progress/hold are recorded for audit but must NOT move the order — and still 2xx."""
    order_id = await _seed_order(
        maker,
        order_number=f"ORD-{status.upper()}",
        payment_uuid=f"u-{status}",
        payment_status="pending",
    )
    resp = await client.post(
        WEBHOOK_URL,
        json=_webhook_body(uuid=f"u-{status}", invoice_id=f"ORD-{status.upper()}", status=status),
    )
    assert resp.status_code == 200
    assert await _event_count(maker, f"u-{status}", status) == 1  # recorded
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "pending"  # untouched (armed but not advanced)
    assert order.status == OrderStatus.PENDING


async def test_webhook_unknown_invoice_still_acknowledged(client: AsyncClient, maker) -> None:
    """An unmatched webhook must return 2xx so Multicard stops its 5x retry loop."""
    resp = await client.post(
        WEBHOOK_URL, json=_webhook_body(uuid="u-x", invoice_id="ORD-NONE", status="success")
    )
    assert resp.status_code == 200


# --------------------------------------------------------------------------- #
# race: two concurrent success callbacks → applied exactly once
# --------------------------------------------------------------------------- #


async def test_race_two_success_callbacks_apply_once(
    client: AsyncClient, maker, sent_messages: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two success callbacks for the same order forced to overlap → exactly one applies.

    The barrier wraps process_payment_event and releases both racers only once both have entered
    — forcing a real transaction overlap (asyncio.gather alone does not reliably interleave on
    localhost Postgres). Requests share the ``client`` fixture, whose override gives each a fresh
    committed session — the same DB the seed used.

    What holds under the race is the ``UNIQUE(payment_uuid, status)`` + ``ON CONFLICT DO NOTHING``
    gate: exactly one insert wins, the loser's ``inserted is None`` short-circuits before any
    order write or notification. Mutation that turns this red: drop ``on_conflict_do_nothing``
    (already covered by ``test_duplicate_success_callback_is_noop``; here it is proven under
    genuine concurrency, not just sequential replay). NOTE: removing ``.with_for_update()`` does
    NOT turn this red — the UNIQUE gate carries idempotency on its own; see the router/lifecycle
    note on why the lock is kept for the money path and Stage 6.
    """
    order_id = await _seed_order(
        maker, order_number="ORD-RACE", payment_uuid="u-race", payment_status="pending"
    )
    body = _callback_body(uuid="u-race", invoice_id="ORD-RACE")

    from src.payments import lifecycle as lc

    real_process = lc.process_payment_event
    barrier = asyncio.Barrier(2)

    async def _barriered(db: AsyncSession, **kwargs: object) -> lc.EventOutcome:
        await asyncio.wait_for(barrier.wait(), timeout=10)  # fail loudly instead of hanging CI
        return await real_process(db, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("src.payments.router.process_payment_event", _barriered)

    async def _attempt() -> int:
        resp = await client.post(CALLBACK_URL, json=body)
        return resp.status_code

    results = await asyncio.gather(_attempt(), _attempt())

    assert results == [200, 200]  # both acknowledged
    assert await _event_count(maker, "u-race", "success") == 1  # applied exactly once
    assert len(sent_messages) == 1  # notified exactly once
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "paid"


# --------------------------------------------------------------------------- #
# attempt awareness — the Gate-2 blocker: events from an abandoned attempt
# --------------------------------------------------------------------------- #


async def test_late_error_from_abandoned_attempt_does_not_cancel_active_order(
    client: AsyncClient, maker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE blocker (attempt guard, isolated): order has an ACTIVE unpaid attempt B; a late error
    webhook for the abandoned attempt A arrives.

    The order stays pending — the customer is still paying via B; the error is A's, recorded as an
    anomaly, not applied. Seeded PENDING (not paid) on purpose: on a paid order the state guard
    would also block the error, so this scenario isolates the attempt guard itself.
    Mutation: drop the error/revert attempt guard → order becomes failed+cancelled → red.
    """
    order_id = await _seed_order(
        maker,
        order_number="ORD-2ATT",
        payment_uuid="uuid-B",  # active attempt, still unpaid
        payment_status="pending",
    )
    _confirm_webhook(monkeypatch, uuid="uuid-A", status="error")

    resp = await client.post(
        WEBHOOK_URL,
        json=_webhook_body(uuid="uuid-A", invoice_id="ORD-2ATT", status="error"),
    )

    assert resp.status_code == 200  # acknowledged so Multicard stops retrying
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "pending"  # NOT cancelled — B is still in flight
    assert order.status == OrderStatus.PENDING
    assert await _event_count(maker, "uuid-A", "error") == 1  # recorded as anomaly


async def test_late_success_from_non_active_attempt_is_ignored(
    client: AsyncClient, maker, sent_messages: list[str]
) -> None:
    """A late success callback for an abandoned attempt must not re-stamp a paid order or re-notify."""
    order_id = await _seed_order(
        maker,
        order_number="ORD-LATESUC",
        payment_uuid="uuid-B",
        payment_status="paid",
        order_status=OrderStatus.CONFIRMED,
    )

    resp = await client.post(
        CALLBACK_URL, json=_callback_body(uuid="uuid-A", invoice_id="ORD-LATESUC")
    )

    assert resp.status_code == 200
    order = await _committed_order(maker, order_id)
    assert order.payment_uuid == "uuid-B"  # not overwritten by the stale attempt
    assert sent_messages == []  # no second "paid" notification


async def test_late_success_from_older_attempt_settles_unpaid_order(
    client: AsyncClient, maker, sent_messages: list[str]
) -> None:
    """The POSITIVE side of the attempt-guard narrowing (Gate-2 MINOR): a success whose uuid is an
    OLDER attempt than the active one still settles a NOT-yet-paid order — that money really arrived.

    Order is armed on attempt B (pending) but the customer actually paid the earlier attempt A in a
    stale tab; the A success callback must mark it paid, not be discarded. This is exactly the
    branch the guard was narrowed to allow — without this test a regression that re-broadens the
    attempt guard onto success (silently dropping a real payment) would pass unnoticed.
    Mutation: re-add ``and payment_uuid == order.payment_uuid`` to the success path → order stays
    pending, this test goes red.
    """
    order_id = await _seed_order(
        maker,
        order_number="ORD-OLDPAID",
        payment_uuid="uuid-B",  # active attempt, still unpaid
        payment_status="pending",
    )

    resp = await client.post(
        CALLBACK_URL, json=_callback_body(uuid="uuid-A", invoice_id="ORD-OLDPAID")
    )

    assert resp.status_code == 200, resp.text
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "paid"  # the real payment settled the order
    assert order.status == OrderStatus.CONFIRMED
    assert order.payment_uuid == "uuid-A"  # settled by the attempt that actually paid
    assert order.paid_at is not None
    assert len(sent_messages) == 1  # admins notified once


async def test_webhook_success_is_not_trusted(client: AsyncClient, maker, sent_messages) -> None:
    """A success arriving via the SHA1 webhook (unsigned status → spoofable) must NOT mark paid.

    The webhook records the event but does not settle the order — settlement is the MD5 callback's
    job. Mutation: apply success from the webhook (``trust_success`` ignored) → order paid → red.
    """
    order_id = await _seed_order(
        maker, order_number="ORD-WHSUC", payment_uuid="u-whsuc", payment_status="pending"
    )

    resp = await client.post(
        WEBHOOK_URL, json=_webhook_body(uuid="u-whsuc", invoice_id="ORD-WHSUC", status="success")
    )

    assert resp.status_code == 200
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "pending"  # NOT paid
    assert await _event_count(maker, "u-whsuc", "success") == 1  # recorded for audit
    assert sent_messages == []


async def test_cancelled_order_not_resurrected_by_late_success(client: AsyncClient, maker) -> None:
    """A success on the active attempt of an already-cancelled order must not revive it.

    State-machine guard: success applies only out of unpaid/pending, never out of a terminal
    failed/cancelled. Mutation: drop the state guard → cancelled order flips to paid+confirmed → red.
    """
    order_id = await _seed_order(
        maker,
        order_number="ORD-CANC",
        payment_uuid="u-canc",
        payment_status="failed",
        order_status=OrderStatus.CANCELLED,
    )

    resp = await client.post(
        CALLBACK_URL, json=_callback_body(uuid="u-canc", invoice_id="ORD-CANC")
    )

    assert resp.status_code == 200  # recorded, acknowledged
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "failed"  # not resurrected
    assert order.status == OrderStatus.CANCELLED


async def test_late_success_on_cancelled_partial_writes_no_redeem(
    client: AsyncClient, maker, sent_messages: list[str]
) -> None:
    """A late success on an already-cancelled PARTIAL order commits NO redeem debit (Model-1 M1).

    This is the late-success-after-cleanup scenario the Gate-1 review asked to pin: the lazy cleanup
    cancelled the order and deleted its invoice, then a stray success callback arrives on the old
    attempt. ``commit_redeem`` sits INSIDE the state-guarded success branch (success applies only out
    of unpaid/pending), so the guard stops it before any debit — the balance is untouched and the
    order stays cancelled. Mutation: move the ``commit_redeem`` call OUTSIDE the state guard in
    ``process_payment_event`` (e.g. right after the amount check) → a -45 000 row is written against
    the cancelled order and the balance drops → red.
    """
    order_id = await _seed_order(
        maker,
        order_number="ORD-LATE-CANC",
        total=Decimal("50000.00"),
        points_applied=45_000,
        payment_uuid="u-late-canc",
        payment_status="failed",
        order_status=OrderStatus.CANCELLED,
        points_balance=45_000,
    )
    remainder_tiyins = (50_000 - 45_000) * 100

    resp = await client.post(
        CALLBACK_URL,
        json=_callback_body(
            uuid="u-late-canc", invoice_id="ORD-LATE-CANC", amount=remainder_tiyins
        ),
    )

    assert resp.status_code == 200  # recorded, acknowledged
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "failed"  # not resurrected
    assert order.status == OrderStatus.CANCELLED
    # The state guard stopped the success BEFORE commit_redeem — no debit, balance untouched.
    assert await _committed_redeem_rows(maker) == []
    assert await _committed_balance(maker, order.user_id) == 45_000
    assert sent_messages == []


# --------------------------------------------------------------------------- #
# create_payment TOCTOU — arming an attempt must not clobber a concurrent paid
# --------------------------------------------------------------------------- #


async def test_arm_payment_refuses_when_already_paid(maker) -> None:
    """The atomic guard behind create_payment's TOCTOU fix, tested directly.

    A concurrent success callback can pay the order during the (up to 30s) Multicard call; arming
    the new attempt afterwards must be a no-op, not a blind overwrite back to pending.
    Mutation: drop ``WHERE payment_status != 'paid'`` → returns True and clobbers paid → red.
    """
    from src.orders import service as orders_service

    order_id = await _seed_order(
        maker,
        order_number="ORD-ARM-PAID",
        payment_uuid="uuid-real",
        payment_status="paid",
        order_status=OrderStatus.CONFIRMED,
    )

    async with maker() as session, session.begin():
        armed = await orders_service.try_arm_payment_attempt(session, order_id, "uuid-new")

    assert armed is False
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "paid"  # untouched
    assert order.payment_uuid == "uuid-real"  # not overwritten


async def test_arm_payment_succeeds_when_not_paid(maker) -> None:
    """Arming a fresh attempt on an unpaid order updates uuid + moves it to pending."""
    order_id = await _seed_order(maker, order_number="ORD-ARM-OK", payment_status="unpaid")

    from src.orders import service as orders_service

    async with maker() as session, session.begin():
        armed = await orders_service.try_arm_payment_attempt(session, order_id, "uuid-fresh")

    assert armed is True
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "pending"
    assert order.payment_uuid == "uuid-fresh"


async def test_success_callback_writes_timezone_aware_paid_at(
    client: AsyncClient, maker: async_sessionmaker[AsyncSession], prod_timezone: None
) -> None:
    """A successful card callback stamps ``paid_at`` — it must not drift backwards.

    The defect only reproduces under the application server's timezone, which ``prod_timezone``
    sets: under ``TZ=UTC`` a naive write is indistinguishable from an aware one, so this test would
    pass against broken code.
    """
    order_id = await _seed_order(maker, order_number="ORD-TZ", payment_uuid="u-tz")
    before = datetime.now(UTC)

    resp = await client.post(CALLBACK_URL, json=_callback_body(uuid="u-tz", invoice_id="ORD-TZ"))

    assert resp.status_code == 200, resp.text
    order = await _committed_order(maker, order_id)
    assert order.paid_at is not None
    assert timedelta(0) <= order.paid_at - before < timedelta(minutes=5), (
        f"отметка уехала: paid_at={order.paid_at}, момент операции={before}"
    )


async def _receipt(sm: async_sessionmaker[AsyncSession], uuid: str) -> dict[str, object]:
    """Read every retained field independently of the request identity map."""
    async with sm() as session:
        row = await session.scalar(
            select(PaymentEvent).where(
                PaymentEvent.payment_uuid == uuid, PaymentEvent.status == "success"
            )
        )
        assert row is not None
        return {column.name: getattr(row, column.name) for column in PaymentEvent.__table__.columns}


@pytest.mark.parametrize("points", [0, 45000])
@pytest.mark.parametrize("hint_first", [True, False])
async def test_hint_callback_collision_settles_once_and_preserves_receipt(
    client: AsyncClient, maker, sent_messages, monkeypatch, points: int, hint_first: bool
) -> None:
    outcomes = []

    async def observe(db, **kwargs):
        result = await lc.process_payment_event(db, **kwargs)
        outcomes.append((result.newly_processed, result.applied_success))
        return result

    monkeypatch.setattr(payment_router, "process_payment_event", observe)
    order_id = await _seed_order(
        maker,
        order_number="ORD-COLLISION",
        payment_uuid="newer-attempt",
        payment_status="pending",
        points_applied=points,
        points_balance=points,
    )
    amount = (50000 - points) * 100
    hint = _webhook_body(
        uuid="older-attempt", invoice_id="ORD-COLLISION", status="success", amount=1
    )
    callback = _callback_body(uuid="older-attempt", invoice_id="ORD-COLLISION", amount=amount)
    first_url, first_body = (WEBHOOK_URL, hint) if hint_first else (CALLBACK_URL, callback)
    response = await client.post(first_url, json=first_body)
    assert response.status_code == 200
    assert response.json() == {"success": True}
    retained = await _receipt(maker, "older-attempt")
    if hint_first:
        assert (await _committed_order(maker, order_id)).payment_status == "pending"
        assert await _committed_redeem_rows(maker) == []
        assert sent_messages == []
    second = await client.post(
        CALLBACK_URL if hint_first else WEBHOOK_URL, json=callback if hint_first else hint
    )
    replay = await client.post(CALLBACK_URL, json=callback)
    assert second.status_code == replay.status_code == 200
    assert second.json() == replay.json() == {"success": True}
    settled = await _committed_order(maker, order_id)
    assert (settled.payment_uuid, settled.payment_status, settled.status) == (
        "older-attempt",
        "paid",
        OrderStatus.CONFIRMED,
    )
    assert settled.paid_at is not None
    assert settled.paid_at.tzinfo is not None
    debits = await _committed_redeem_rows(maker)
    assert [row.amount for row in debits] == ([-points] if points else [])
    assert await _committed_balance(maker, settled.user_id) == 0
    assert len(sent_messages) == 1
    assert await _receipt(maker, "older-attempt") == retained
    assert outcomes == (
        [(True, False), (False, True), (False, False)]
        if hint_first
        else [(True, True), (False, False), (False, False)]
    )
    async with maker() as reader:
        for model in (PaidOutbox, K2Delivery, NoticeDelivery):
            assert await reader.scalar(select(func.count()).select_from(model)) == 0


async def _seed_hint(maker, invoice="ORD-RECOVER", uuid="hint", **changes):
    payload = _webhook_body(uuid=uuid, invoice_id=invoice, status="success")
    values = {
        "payment_uuid": uuid,
        "invoice_id": invoice,
        "status": "success",
        "amount": DUE_TIYINS,
        "payload": payload,
    }
    values.update(changes)
    async with maker.begin() as session:
        session.add(PaymentEvent(**values))
    return await _receipt(maker, uuid)


@pytest.mark.parametrize(
    "defect",
    [
        "null",
        "list",
        "string",
        "missing-key",
        "extra-key",
        "callback",
        "uuid-type",
        "invoice-type",
        "status-type",
        "sign-type",
        "amount-bool",
        "amount-float",
        "amount-string",
        "uuid-value",
        "invoice-value",
        "status-value",
        "amount-value",
        "uppercase-sign",
        "short-sign",
        "row-invoice",
    ],
)
async def test_unrecognized_receipt_preserves_duplicate_precedence(
    client, maker, sent_messages, defect
):
    order_id = await _seed_order(maker, order_number="ORD-RECOVER", payment_status="pending")
    payload = _webhook_body(uuid="hint", invoice_id="ORD-RECOVER", status="success")
    row_changes = {}
    if defect in ("null", "list", "string"):
        payload = {"null": None, "list": [], "string": "opaque"}[defect]
    elif defect == "missing-key":
        payload.pop("sign")
    elif defect == "extra-key":
        payload["extra"] = 1
    elif defect == "callback":
        payload = _callback_body(uuid="hint", invoice_id="ORD-RECOVER")
    elif defect == "row-invoice":
        row_changes["invoice_id"] = "OTHER-INVOICE"
    elif defect.endswith("-type"):
        payload[defect.removesuffix("-type")] = 1
    elif defect.startswith("amount-"):
        payload["amount"] = {
            "amount-bool": True,
            "amount-float": float(DUE_TIYINS),
            "amount-string": str(DUE_TIYINS),
            "amount-value": 123,
        }[defect]
    elif defect.endswith("-value"):
        payload[defect.removesuffix("-value")] = "different"
    else:
        payload["sign"] = "A" * 40 if defect == "uppercase-sign" else "a" * 39
    retained = await _seed_hint(maker, payload=payload, **row_changes)
    response = await client.post(
        CALLBACK_URL, json=_callback_body(uuid="hint", invoice_id="ORD-RECOVER", amount=1)
    )
    assert response.status_code == 200
    assert response.json() == {"success": True}
    order = await _committed_order(maker, order_id)
    assert (order.payment_status, order.status, order.paid_at) == (
        "pending",
        OrderStatus.PENDING,
        None,
    )
    assert await _receipt(maker, "hint") == retained
    assert await _committed_redeem_rows(maker) == []
    assert sent_messages == []


@pytest.mark.parametrize("collision", [False, True])
@pytest.mark.parametrize(
    ("state", "cancelled"),
    [
        ("pending", False),
        ("pending", True),
        ("failed", False),
        ("refunded", False),
        ("paid", False),
    ],
)
async def test_amount_and_state_precedence(
    client, maker, sent_messages, collision, state, cancelled
):
    order_id = await _seed_order(
        maker,
        order_number="ORD-RECOVER",
        payment_status=state,
        order_status=OrderStatus.CANCELLED if cancelled else OrderStatus.PENDING,
    )
    retained = await _seed_hint(maker) if collision else None
    wrong = await client.post(
        CALLBACK_URL, json=_callback_body(uuid="hint", invoice_id="ORD-RECOVER", amount=1)
    )
    eligible = state == "pending" and not cancelled
    expected = 400 if not collision or eligible else 200
    assert wrong.status_code == expected
    assert wrong.json() == ({"detail": "Amount mismatch"} if expected == 400 else {"success": True})
    assert (await _committed_order(maker, order_id)).payment_status == state
    if retained is not None:
        assert await _receipt(maker, "hint") == retained
    else:
        assert await _event_count(maker, "hint", "success") == 0
    correct = await client.post(
        CALLBACK_URL, json=_callback_body(uuid="hint", invoice_id="ORD-RECOVER")
    )
    assert correct.status_code == 200
    assert correct.json() == {"success": True}
    current = await _committed_order(maker, order_id)
    assert current.payment_status == ("paid" if eligible else state)
    assert current.status == (
        OrderStatus.CONFIRMED
        if eligible
        else OrderStatus.CANCELLED
        if cancelled
        else OrderStatus.PENDING
    )
    assert len(sent_messages) == int(eligible)


async def test_validation_signature_and_unknown_order_precede_receipt(client, maker, sent_messages):
    await _seed_order(maker, order_number="ORD-RECOVER")
    retained = await _seed_hint(maker)
    for url, body in (
        (CALLBACK_URL, _callback_body(uuid="hint", invoice_id="UNKNOWN")),
        (WEBHOOK_URL, _webhook_body(uuid="hint", invoice_id="UNKNOWN", status="success")),
    ):
        invalid = copy.deepcopy(body)
        invalid.pop("amount")
        response = await client.post(url, json=invalid)
        assert response.status_code == 422
        invalid = {**body, "sign": "invalid"}
        response = await client.post(url, json=invalid)
        assert response.status_code == 400
        assert response.json() == {"detail": "Invalid signature"}
        response = await client.post(url, json=body)
        assert response.status_code == (404 if url == CALLBACK_URL else 200)
        assert response.json() == (
            {"detail": "Order not found"} if url == CALLBACK_URL else {"success": True}
        )
    assert await _receipt(maker, "hint") == retained
    assert sent_messages == []


@pytest.mark.parametrize("recovered", [False, True])
async def test_paid_revert_rearm_replay_retains_history_guard(
    client, maker, sent_messages, monkeypatch: pytest.MonkeyPatch, recovered
):
    order_id = await _seed_order(
        maker,
        order_number="ORD-RECOVER",
        points_applied=10000,
        points_balance=30000,
        payment_uuid="hint",
        payment_status="pending",
    )
    if recovered:
        await _seed_hint(maker)
    callback = _callback_body(uuid="hint", invoice_id="ORD-RECOVER", amount=4_000_000)
    assert (await client.post(CALLBACK_URL, json=callback)).status_code == 200
    paid_at = (await _committed_order(maker, order_id)).paid_at
    retained = await _receipt(maker, "hint")
    _confirm_webhook(monkeypatch, uuid="hint", status="revert")
    assert (
        await client.post(
            WEBHOOK_URL, json=_webhook_body(uuid="hint", invoice_id="ORD-RECOVER", status="revert")
        )
    ).status_code == 200
    from src.orders.service import try_arm_payment_attempt

    async with maker.begin() as session:
        assert await try_arm_payment_attempt(session, order_id, "rearmed")
        order = await session.get(Order, order_id)
        order.status = OrderStatus.PENDING  # Isolate paid_at from the cancellation guard.
    for amount in (1, 4_000_000):
        response = await client.post(
            CALLBACK_URL,
            json={**callback, "amount": amount, "sign": _md5(STORE_ID, "ORD-RECOVER", amount)},
        )
        assert response.status_code == 200
        assert response.json() == {"success": True}
    order = await _committed_order(maker, order_id)
    assert (order.payment_uuid, order.payment_status, order.status, order.paid_at) == (
        "rearmed",
        "pending",
        OrderStatus.PENDING,
        paid_at,
    )
    assert [row.amount for row in await _committed_redeem_rows(maker)] == [-10000]
    assert len(sent_messages) == 1
    assert await _receipt(maker, "hint") == retained


@pytest.mark.parametrize("kind", ["same-hint", "distinct-hints", "hint-callback"])
async def test_preloaded_overlap_uses_current_locked_state(
    client, maker, sent_messages, monkeypatch, kind
):
    order_id = await _seed_order(
        maker,
        order_number="ORD-RECOVER",
        points_applied=10000,
        points_balance=30000,
        payment_status="pending",
    )
    if kind != "hint-callback":
        await _seed_hint(maker)
        if kind == "distinct-hints":
            await _seed_hint(maker, uuid="other-hint")
    barrier = asyncio.Barrier(2)
    second_started = asyncio.Event()
    second_selected = asyncio.Event()
    first_locked = asyncio.Event()
    calls = 0
    outcomes = []
    observed = []
    backend = {}

    async def controlled(db, **kwargs):
        nonlocal calls
        index = calls
        calls += 1
        backend[index] = await db.scalar(text("SELECT pg_backend_pid()"))
        await asyncio.wait_for(barrier.wait(), 10)
        execute = db.execute
        first_select = True

        async def select_under_observation(statement, *args, **kw):
            nonlocal first_select
            if not first_select:
                return await execute(statement, *args, **kw)
            first_select = False
            if index == 1:
                await asyncio.wait_for(first_locked.wait(), 10)
                second_started.set()
            result = await execute(statement, *args, **kw)
            if index == 1:
                second_selected.set()
            else:
                first_locked.set()
                await asyncio.wait_for(second_started.wait(), 10)
                # Observe an actual PostgreSQL lock wait, or the lost-lock counterexample.
                async with maker() as observer, asyncio.timeout(10):
                    while True:
                        blocked = await observer.scalar(
                            text("SELECT cardinality(pg_blocking_pids(:pid))"), {"pid": backend[1]}
                        )
                        if blocked or second_selected.is_set():
                            observed.append("blocked" if blocked else "selected-without-lock")
                            break
            return result

        monkeypatch.setattr(db, "execute", select_under_observation)
        outcome = await lc.process_payment_event(db, **kwargs)
        outcomes.append(outcome.applied_success)
        return outcome

    monkeypatch.setattr(payment_router, "process_payment_event", controlled)
    callback = _callback_body(uuid="hint", invoice_id="ORD-RECOVER", amount=4_000_000)
    second = {**callback, "uuid": "other-hint"} if kind == "distinct-hints" else callback
    first_url, first_body = (
        (WEBHOOK_URL, _webhook_body(uuid="hint", invoice_id="ORD-RECOVER", status="success"))
        if kind == "hint-callback"
        else (CALLBACK_URL, callback)
    )
    responses = await asyncio.wait_for(
        asyncio.gather(
            client.post(first_url, json=first_body), client.post(CALLBACK_URL, json=second)
        ),
        20,
    )
    assert [response.status_code for response in responses] == [200, 200]
    assert all(response.json() == {"success": True} for response in responses)
    assert sum(outcomes) == 1
    assert [row.amount for row in await _committed_redeem_rows(maker)] == [-10000]
    assert len(sent_messages) == 1
    assert (await _committed_order(maker, order_id)).payment_status == "paid"
    assert observed == ["blocked"]


@pytest.mark.parametrize("recovered", [False, True])
@pytest.mark.parametrize(
    "failure", ["record", "paid-commit", "integrity", "operational", "unknown", "commit-ack"]
)
async def test_financial_commit_errors_and_unknown_ack(
    client, maker, sent_messages, monkeypatch, recovered, failure
):
    order_id = await _seed_order(
        maker,
        order_number="ORD-RECOVER",
        payment_status="pending",
        points_applied=10000,
        points_balance=30000,
    )
    retained = await _seed_hint(maker) if recovered else None
    errors = {
        "record": (PaidUnavailableError(), 503, {"detail": {"code": "referral_paid_unavailable"}}),
        "paid-commit": (
            PaidUnavailableError(),
            503,
            {"detail": {"code": "referral_paid_unavailable"}},
        ),
        "integrity": (
            IntegrityError("synthetic", {}, Exception()),
            409,
            {"detail": "Data conflict: duplicate or constraint violation"},
        ),
        "operational": (
            OperationalError("synthetic", {}, Exception()),
            503,
            {"detail": "Database temporarily unavailable"},
        ),
        "unknown": (RuntimeError("synthetic"), 500, {"detail": "Internal server error"}),
        "commit-ack": (
            RuntimeError("unknown acknowledgement"),
            500,
            {"detail": "Internal server error"},
        ),
    }
    error, code, body = errors[failure]
    real_commit = payment_router.commit_paid
    real_record = lc.record_paid

    async def fail(db, *args):
        if failure == "commit-ack":
            await real_commit(db)
        raise error

    if failure == "record":
        monkeypatch.setattr(lc, "record_paid", fail)
    else:
        monkeypatch.setattr(payment_router, "commit_paid", fail)
    callback = _callback_body(uuid="hint", invoice_id="ORD-RECOVER", amount=4_000_000)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
    ) as http:
        response = await http.post(CALLBACK_URL, json=callback)
    assert response.status_code == code
    assert response.json() == body
    settled = failure == "commit-ack"
    order = await _committed_order(maker, order_id)
    assert order.payment_status == ("paid" if settled else "pending")
    assert [row.amount for row in await _committed_redeem_rows(maker)] == (
        [-10000] if settled else []
    )
    assert await _event_count(maker, "hint", "success") == int(recovered or settled)
    if retained:
        assert await _receipt(maker, "hint") == retained
    assert sent_messages == []
    monkeypatch.setattr(payment_router, "commit_paid", real_commit)
    if failure == "record":
        monkeypatch.setattr(lc, "record_paid", real_record)
    if settled:
        retry = await client.post(CALLBACK_URL, json=callback)
        assert retry.status_code == 200
        assert retry.json() == {"success": True}
        assert [row.amount for row in await _committed_redeem_rows(maker)] == [-10000]
        assert sent_messages == []


@pytest.mark.parametrize("applied", [False, True])
async def test_wire_response_is_distinct_from_dependency_finalization(
    client, maker, sent_messages, applied
):
    order_id = await _seed_order(maker, order_number="ORD-FINALIZER", payment_status="pending")

    async def late_failure():
        async with maker() as session:
            try:
                yield session
                raise OperationalError("synthetic finalizer", {}, Exception())
            except Exception:
                await session.rollback()
                raise

    old = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = late_failure
    body = (
        _callback_body(uuid="finalizer", invoice_id="ORD-FINALIZER")
        if applied
        else _webhook_body(uuid="finalizer", invoice_id="ORD-FINALIZER", status="success")
    )
    path = CALLBACK_URL if applied else WEBHOOK_URL
    sent = []

    async def receive():
        return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}

    async def send(message):
        sent.append(message)

    try:
        with pytest.raises(RuntimeError, match="response already started") as raised:
            await app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "method": "POST",
                    "path": path,
                    "raw_path": path.encode(),
                    "query_string": b"",
                    "headers": [(b"content-type", b"application/json")],
                    "scheme": "http",
                    "server": ("test", 80),
                    "client": ("127.0.0.1", 0),
                    "http_version": "1.1",
                },
                receive,
                send,
            )
        assert raised.value is not None
    finally:
        app.dependency_overrides[get_db] = old
    assert [message["status"] for message in sent if message["type"] == "http.response.start"] == [
        200
    ]
    assert json.loads(
        b"".join(
            message.get("body", b"") for message in sent if message["type"] == "http.response.body"
        )
    ) == {"success": True}
    order = await _committed_order(maker, order_id)
    assert order.payment_status == ("paid" if applied else "pending")
    assert await _event_count(maker, "finalizer", "success") == int(applied)
    assert len(sent_messages) == int(applied)


@pytest.mark.parametrize("failure", ["none", "transport", "notifier", "owner", "read"])
async def test_actual_notifier_runs_after_commit_and_optional_failures_are_isolated(
    client, maker, monkeypatch, failure
):
    from src.notifications.service import TelegramNotifier, telegram_notifier

    order_id = await _seed_order(
        maker,
        order_number="ORD-RECOVER",
        payment_status="pending",
        points_applied=10000,
        points_balance=30000,
    )
    await _seed_hint(maker)
    posts = []
    reads = []
    monkeypatch.setattr(
        telegram_notifier, "send_message", TelegramNotifier.send_message.__get__(telegram_notifier)
    )

    async def post(message, parse_mode):
        # The real formatter/send adapter reaches this transport boundary with a durable,
        # unlocked settlement. Another connection could not lock it NOWAIT before commit.
        async with maker.begin() as observer:
            order = await observer.scalar(
                select(Order).where(Order.id == order_id).with_for_update(nowait=True)
            )
            assert order.payment_status == "paid"
            assert (
                await observer.scalar(
                    select(func.count())
                    .select_from(PointLedger)
                    .where(PointLedger.reason == "redeem")
                )
                == 1
            )
        posts.append((message, parse_mode))
        if failure == "transport":
            raise RuntimeError("synthetic transport failure")

    monkeypatch.setattr(telegram_notifier, "_post_message", post)
    real_owner = payment_router.owns_paid
    real_load = payment_router.orders_service.get_order_with_items

    async def owner(db, order_id):
        reads.append(db)
        value = await real_owner(db, order_id)
        if failure == "owner":
            raise OperationalError("synthetic owner failure", {}, Exception())
        return value

    async def load(db, order_id):
        value = await real_load(db, order_id)
        if failure == "read":
            raise OperationalError("synthetic read failure", {}, Exception())
        return value

    async def broken_notifier(**kwargs):
        raise RuntimeError("synthetic formatter/notifier failure")

    monkeypatch.setattr(payment_router, "owns_paid", owner)
    monkeypatch.setattr(payment_router.orders_service, "get_order_with_items", load)
    if failure == "notifier":
        monkeypatch.setattr(telegram_notifier, "notify_payment_success", broken_notifier)
    callback = _callback_body(uuid="hint", invoice_id="ORD-RECOVER", amount=4_000_000)
    for _ in range(2):
        response = await client.post(CALLBACK_URL, json=callback)
        assert response.status_code == 200
        assert response.json() == {"success": True}
    assert len(posts) == (2 if failure == "transport" else 1 if failure == "none" else 0)
    assert len(reads) == 1
    assert not reads[0].in_transaction()
    assert (await _committed_order(maker, order_id)).payment_status == "paid"
    assert [row.amount for row in await _committed_redeem_rows(maker)] == [-10000]


async def test_recovered_ordinary_shortfall_is_settled_without_retrying_debit(
    client, maker, sent_messages, caplog
):
    order_id = await _seed_order(
        maker,
        order_number="ORD-RECOVER",
        points_applied=10000,
        points_balance=1,
        payment_status="pending",
    )
    await _seed_hint(maker)
    body = _callback_body(uuid="hint", invoice_id="ORD-RECOVER", amount=4_000_000)
    response = await client.post(CALLBACK_URL, json=body)
    assert response.status_code == 200
    assert response.json() == {"success": True}
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "paid"
    assert "commit_redeem shortfall" in caplog.text
    async with maker.begin() as session:
        session.add(PointLedger(user_id=order.user_id, amount=20000, reason=LedgerReason.CASHBACK))
    assert (await client.post(CALLBACK_URL, json=body)).status_code == 200
    assert await _committed_redeem_rows(maker) == []
    assert len(sent_messages) == 1
