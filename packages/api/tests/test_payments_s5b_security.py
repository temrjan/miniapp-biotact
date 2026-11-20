"""Webhook provider confirmation (A5-05) and Mini App payment URL validation (A5-06).

Covers PR-S5b requirements W-01..W-06 and P-01..P-05 against the real FastAPI routes and
PostgreSQL test database, with external Multicard and Telegram boundaries replaced at their ports.
"""

import hashlib
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.database import get_db
from src.main import app
from src.notifications.service import telegram_notifier
from src.orders.models import Order, OrderItem, OrderStatus
from src.payments import size_stock
from src.payments.models import PaymentEvent
from src.payments.schemas import MultiCardPaymentResponse, PaymentCreateData
from src.payments.service import multicard_client
from src.products.models import Product, SizeStockRecord
from src.users.models import User
from tests.conftest import _make_init_data

TEST_DATABASE_URL = settings.database_url.rsplit("/", 1)[0] + "/biotact_test_db"
SECRET = "ab" * 16
BOT_TOKEN = "cd" * 16

CREATE_URL = "/api/v1/payments/create"
WEBHOOK_URL = "/api/v1/payments/webhook"

OWNER_TELEGRAM_ID = 55001
STRANGER_TELEGRAM_ID = 55002

INVOICE_UUID = "527b7ead-b587-11f0-af7c-005056b4367d"
OTHER_UUID = "627b7ead-b587-11f0-af7c-005056b4367d"

ORDER_TOTAL = Decimal("69000.00")
DUE_TIYINS = 6_900_000
PAID_AT = datetime(2026, 11, 20, 7, 0, tzinfo=UTC)

CALLBACK_ORIGIN = "https://callback.example.test"
MINIAPP_ORIGIN = "https://app.example.test"


def _headers(telegram_id: int = OWNER_TELEGRAM_ID) -> dict[str, str]:
    return {"X-Init-Data": _make_init_data(BOT_TOKEN, {"id": telegram_id, "first_name": "Buyer"})}


def _sha1(uuid: str, invoice_id: str, amount: int) -> str:
    return hashlib.sha1(f"{uuid}{invoice_id}{amount}{SECRET}".encode()).hexdigest()


def _webhook_body(
    *,
    uuid: str = INVOICE_UUID,
    invoice_id: str,
    status: str,
    amount: int = DUE_TIYINS,
    sign: str | None = None,
) -> dict[str, object]:
    return {
        "uuid": uuid,
        "invoice_id": invoice_id,
        "amount": amount,
        "status": status,
        "sign": sign if sign is not None else _sha1(uuid, invoice_id, amount),
    }


def _set_miniapp_origin(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    if hasattr(settings, "miniapp_public_origin"):
        monkeypatch.setattr(settings, "miniapp_public_origin", value)


@pytest.fixture(autouse=True)
def _pin_external_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", BOT_TOKEN)
    monkeypatch.setattr(settings, "multicard_store_id", "6")
    monkeypatch.setattr(multicard_client, "secret", SECRET)


@pytest.fixture(autouse=True)
def sent_messages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    captured: list[str] = []

    async def _capture(message: str, parse_mode: str = "HTML") -> bool:
        captured.append(message)
        return True

    monkeypatch.setattr(telegram_notifier, "send_message", _capture)
    return captured


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield sm
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client(
    maker: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_db, None)


async def _seed_order(
    sm: async_sessionmaker[AsyncSession],
    *,
    order_number: str,
    telegram_id: int = OWNER_TELEGRAM_ID,
    payment_uuid: str | None = None,
    payment_status: str = "unpaid",
    order_status: OrderStatus = OrderStatus.PENDING,
    paid_at: datetime | None = None,
    with_item: bool = False,
    sized: bool = False,
) -> int:
    async with sm() as session, session.begin():
        user = (
            await session.execute(select(User).where(User.telegram_id == telegram_id))
        ).scalar_one_or_none()
        if user is None:
            user = User(telegram_id=telegram_id, first_name="Buyer")
            session.add(user)
            await session.flush()

        order = Order(
            order_number=order_number,
            user_id=user.id,
            subtotal=ORDER_TOTAL,
            delivery_fee=Decimal("0"),
            total=ORDER_TOTAL,
            payment_uuid=payment_uuid,
            payment_status=payment_status,
            status=order_status,
            paid_at=paid_at,
            size_stock_protocol=size_stock.PROTOCOL if sized else None,
            size_owner_state="not_started" if sized else None,
            delivery_name="Buyer",
            delivery_phone="+998900000001",
            delivery_address="Tashkent",
        )
        session.add(order)
        await session.flush()

        if with_item or sized:
            product = Product(
                name=f"Product {order_number}",
                slug=f"slug-{order_number.lower()}",
                price=ORDER_TOTAL,
                is_in_stock=True,
                is_active=True,
            )
            session.add(product)
            await session.flush()
            session.add(
                OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    product_name=product.name,
                    product_price=ORDER_TOTAL,
                    quantity=1,
                    total=ORDER_TOTAL,
                    size="L" if sized else None,
                )
            )
            await session.flush()

        return order.id


async def _committed_order(sm: async_sessionmaker[AsyncSession], order_id: int) -> Order:
    async with sm() as session:
        return (await session.execute(select(Order).where(Order.id == order_id))).scalar_one()


async def _event_count(
    sm: async_sessionmaker[AsyncSession], uuid: str, status: str | None = None
) -> int:
    async with sm() as session:
        stmt = (
            select(func.count()).select_from(PaymentEvent).where(PaymentEvent.payment_uuid == uuid)
        )
        if status is not None:
            stmt = stmt.where(PaymentEvent.status == status)
        return int((await session.execute(stmt)).scalar_one())


async def _size_record_count(sm: async_sessionmaker[AsyncSession], order_id: int) -> int:
    async with sm() as session:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(SizeStockRecord)
                    .where(SizeStockRecord.order_id == order_id)
                )
            ).scalar_one()
        )


# --------------------------------------------------------------------------- #
# A5-05: W-01..W-06
# --------------------------------------------------------------------------- #


async def test_w01_bad_signature_revert_is_rejected_without_provider_read(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W-01: invalid SHA1 signature on status=revert answers 400 without calling the provider."""
    order_id = await _seed_order(
        maker,
        order_number="ORD-W01-REV",
        payment_uuid=INVOICE_UUID,
        payment_status="paid",
        order_status=OrderStatus.CONFIRMED,
        paid_at=PAID_AT,
    )
    provider_read = AsyncMock(return_value={"uuid": INVOICE_UUID, "payment": {"status": "revert"}})
    monkeypatch.setattr(multicard_client, "get_invoice_status", provider_read)

    resp = await client.post(
        WEBHOOK_URL,
        json=_webhook_body(invoice_id="ORD-W01-REV", status="revert", sign="00" * 20),
    )

    assert resp.status_code == 400
    assert resp.json() == {"detail": "Invalid signature"}
    provider_read.assert_not_awaited()
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "paid"
    assert order.status == OrderStatus.CONFIRMED
    assert await _event_count(maker, INVOICE_UUID) == 0


async def test_w02_webhook_error_requires_provider_confirmation_and_deduplicates(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    sent_messages: list[str],
) -> None:
    """W-02 (a) and (c): unconfirmed error -> 503; confirmed -> 200 cancelled; replay -> 200 no read."""
    order_id = await _seed_order(
        maker,
        order_number="ORD-W02-ERR",
        payment_uuid=INVOICE_UUID,
        payment_status="pending",
        order_status=OrderStatus.PENDING,
    )
    body = _webhook_body(invoice_id="ORD-W02-ERR", status="error")

    unconfirmed = AsyncMock(return_value={"uuid": INVOICE_UUID, "payment": {"status": "progress"}})
    monkeypatch.setattr(multicard_client, "get_invoice_status", unconfirmed)
    first = await client.post(WEBHOOK_URL, json=body)

    assert first.status_code == 503
    assert first.json() == {"detail": {"code": "payment_status_unconfirmed"}}
    unconfirmed.assert_awaited_once_with(INVOICE_UUID)
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "pending"
    assert order.status == OrderStatus.PENDING
    assert await _event_count(maker, INVOICE_UUID, "error") == 0
    assert sent_messages == []

    confirmed = AsyncMock(return_value={"uuid": INVOICE_UUID, "payment": {"status": "error"}})
    monkeypatch.setattr(multicard_client, "get_invoice_status", confirmed)
    second = await client.post(WEBHOOK_URL, json=body)

    assert second.status_code == 200
    assert second.json() == {"success": True}
    confirmed.assert_awaited_once_with(INVOICE_UUID)
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "failed"
    assert order.status == OrderStatus.CANCELLED
    assert await _event_count(maker, INVOICE_UUID, "error") == 1

    expired = AsyncMock(return_value=None)
    monkeypatch.setattr(multicard_client, "get_invoice_status", expired)
    duplicate = await client.post(WEBHOOK_URL, json=body)

    assert duplicate.status_code == 200
    assert duplicate.json() == {"success": True}
    expired.assert_not_awaited()
    assert await _event_count(maker, INVOICE_UUID, "error") == 1


async def test_w02_webhook_revert_requires_provider_confirmation_and_deduplicates(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    sent_messages: list[str],
) -> None:
    """W-02 (b) and (c): unconfirmed revert -> 503; confirmed -> 200 refunded; replay -> 200 no read."""
    order_id = await _seed_order(
        maker,
        order_number="ORD-W02-REV",
        payment_uuid=INVOICE_UUID,
        payment_status="paid",
        order_status=OrderStatus.CONFIRMED,
        paid_at=PAID_AT,
    )
    body = _webhook_body(invoice_id="ORD-W02-REV", status="revert")

    unconfirmed = AsyncMock(return_value={"uuid": INVOICE_UUID, "payment": {"status": "success"}})
    monkeypatch.setattr(multicard_client, "get_invoice_status", unconfirmed)
    first = await client.post(WEBHOOK_URL, json=body)

    assert first.status_code == 503
    assert first.json() == {"detail": {"code": "payment_status_unconfirmed"}}
    unconfirmed.assert_awaited_once_with(INVOICE_UUID)
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "paid"
    assert order.status == OrderStatus.CONFIRMED
    assert order.paid_at == PAID_AT
    assert await _event_count(maker, INVOICE_UUID, "revert") == 0
    assert sent_messages == []

    confirmed = AsyncMock(return_value={"uuid": INVOICE_UUID, "payment": {"status": "revert"}})
    monkeypatch.setattr(multicard_client, "get_invoice_status", confirmed)
    second = await client.post(WEBHOOK_URL, json=body)

    assert second.status_code == 200
    assert second.json() == {"success": True}
    confirmed.assert_awaited_once_with(INVOICE_UUID)
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "refunded"
    assert order.status == OrderStatus.CANCELLED
    assert await _event_count(maker, INVOICE_UUID, "revert") == 1

    expired = AsyncMock(return_value=None)
    monkeypatch.setattr(multicard_client, "get_invoice_status", expired)
    duplicate = await client.post(WEBHOOK_URL, json=body)

    assert duplicate.status_code == 200
    assert duplicate.json() == {"success": True}
    expired.assert_not_awaited()
    assert await _event_count(maker, INVOICE_UUID, "revert") == 1


@pytest.mark.parametrize(
    ("case_id", "outcome", "raises"),
    [
        ("raises", RuntimeError("synthetic token failure"), True),
        ("none", None, False),
        ("uuid-mismatch", {"uuid": OTHER_UUID, "payment": {"status": "error"}}, False),
        ("payment-none", {"uuid": INVOICE_UUID, "payment": None}, False),
        ("empty-dict", {}, False),
        ("payment-empty-dict", {"uuid": INVOICE_UUID, "payment": {}}, False),
        ("non-dict-list", ["error"], False),
        (
            "mismatch-with-pii",
            {
                "uuid": INVOICE_UUID,
                "card_pan": "860000******1111",
                "sms": "654321",
                "card_token": "tok-sensitive",
                "kyc_data": {"pinfl": "987654321"},
                "payment": {
                    "status": "progress",
                    "card_pan": "860000******2222",
                    "card_token": "tok-nested",
                },
            },
            False,
        ),
    ],
    ids=[
        "raises",
        "none",
        "uuid-mismatch",
        "payment-none",
        "empty-dict",
        "payment-empty-dict",
        "non-dict-list",
        "mismatch-with-pii",
    ],
)
async def test_w03_unconfirmed_provider_outcomes_return_503_without_writes(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    sent_messages: list[str],
    caplog: pytest.LogCaptureFixture,
    case_id: str,
    outcome: object,
    raises: bool,
) -> None:
    """W-03 (and W-06 log safety): unconfirmed provider answers return 503 with zero DB writes."""
    order_id = await _seed_order(
        maker,
        order_number="ORD-W03",
        payment_uuid=INVOICE_UUID,
        payment_status="pending",
        order_status=OrderStatus.PENDING,
    )
    double = (
        AsyncMock(side_effect=outcome)  # type: ignore[arg-type]
        if raises
        else AsyncMock(return_value=outcome)
    )
    monkeypatch.setattr(multicard_client, "get_invoice_status", double)
    caplog.set_level(logging.WARNING)

    resp = await client.post(
        WEBHOOK_URL,
        json=_webhook_body(invoice_id="ORD-W03", status="error"),
    )

    assert resp.status_code == 503
    assert resp.json() == {"detail": {"code": "payment_status_unconfirmed"}}
    assert await _event_count(maker, INVOICE_UUID) == 0
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "pending"
    assert order.status == OrderStatus.PENDING
    assert order.payment_uuid == INVOICE_UUID
    assert order.paid_at is None
    assert sent_messages == []

    warnings = [
        rec.getMessage()
        for rec in caplog.records
        if rec.levelno == logging.WARNING and "payment_status_unconfirmed" in rec.getMessage()
    ]
    assert len(warnings) == 1
    assert INVOICE_UUID in warnings[0]
    assert "ORD-W03" in warnings[0]
    assert "error" in warnings[0]
    for secret_fragment in ("860000", "654321", "tok-sensitive", "tok-nested", "987654321"):
        assert secret_fragment not in caplog.text


async def test_w04_provider_read_holds_no_order_lock_and_ends_read_transaction(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W-04: while get_invoice_status runs, FOR UPDATE NOWAIT succeeds and no tx stays open."""
    order_id = await _seed_order(
        maker,
        order_number="ORD-W04",
        payment_uuid=INVOICE_UUID,
        payment_status="pending",
        order_status=OrderStatus.PENDING,
    )
    observed: dict[str, object] = {}

    async def _probe_lock_during_provider_read(uuid: str) -> dict[str, object]:
        assert uuid == INVOICE_UUID
        async with maker() as probe, probe.begin():
            locked_id = await probe.scalar(
                select(Order.id).where(Order.id == order_id).with_for_update(nowait=True)
            )
            idle_in_tx = await probe.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND pid <> pg_backend_pid() "
                    "AND state = 'idle in transaction'"
                )
            )
            observed["locked_id"] = locked_id
            observed["idle_in_tx"] = int(idle_in_tx or 0)
        return {"uuid": INVOICE_UUID, "payment": {"status": "error"}}

    monkeypatch.setattr(multicard_client, "get_invoice_status", _probe_lock_during_provider_read)

    resp = await client.post(
        WEBHOOK_URL,
        json=_webhook_body(invoice_id="ORD-W04", status="error"),
    )

    assert resp.status_code == 200
    assert resp.json() == {"success": True}
    assert observed == {"locked_id": order_id, "idle_in_tx": 0}
    order = await _committed_order(maker, order_id)
    assert order.payment_status == "failed"
    assert order.status == OrderStatus.CANCELLED


@pytest.mark.parametrize(
    ("kind", "webhook_status"),
    [
        ("ordinary", "draft"),
        ("ordinary", "progress"),
        ("ordinary", "hold"),
        ("ordinary", "success"),
        ("unknown", "error"),
        ("unknown", "revert"),
        ("sized", "error"),
    ],
    ids=[
        "draft",
        "progress",
        "hold",
        "success",
        "unknown-error",
        "unknown-revert",
        "size-stock-error",
    ],
)
async def test_w05_non_destructive_unknown_and_sized_webhooks_do_not_read_provider(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    webhook_status: str,
) -> None:
    """W-05: non-destructive statuses, unknown invoices and size-stock hints answer 200 with no read."""
    order_id: int | None = None
    invoice_id = "ORD-W05-UNKNOWN"
    if kind in ("ordinary", "sized"):
        invoice_id = f"ORD-W05-{kind.upper()}-{webhook_status.upper()}"
        order_id = await _seed_order(
            maker,
            order_number=invoice_id,
            payment_uuid=INVOICE_UUID,
            payment_status="pending",
            sized=(kind == "sized"),
        )

    provider_read = AsyncMock(side_effect=AssertionError("Unexpected provider read"))
    monkeypatch.setattr(multicard_client, "get_invoice_status", provider_read)

    resp = await client.post(
        WEBHOOK_URL,
        json=_webhook_body(invoice_id=invoice_id, status=webhook_status),
    )

    assert resp.status_code == 200
    assert resp.json() == {"success": True}
    provider_read.assert_not_awaited()
    if kind == "unknown":
        assert await _event_count(maker, INVOICE_UUID) == 0
    else:
        assert order_id is not None
        assert await _event_count(maker, INVOICE_UUID, webhook_status) == 1
        order = await _committed_order(maker, order_id)
        assert order.payment_status == "pending"
        assert order.status == OrderStatus.PENDING


# --------------------------------------------------------------------------- #
# A5-06: P-01..P-05
# --------------------------------------------------------------------------- #


async def test_p01_callback_url_uses_configured_origin_ignoring_request_headers(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P-01: callback_url is built from payment_callback_base_url, ignoring Host/X-Forwarded-*."""
    order_id = await _seed_order(maker, order_number="ORD-P01", with_item=True)
    monkeypatch.setattr(settings, "payment_callback_base_url", CALLBACK_ORIGIN)
    _set_miniapp_origin(monkeypatch, "https://app.biotact.uz")

    captured: list[PaymentCreateData] = []

    async def _capture_invoice(data: PaymentCreateData) -> MultiCardPaymentResponse:
        captured.append(data)
        return MultiCardPaymentResponse(
            uuid=INVOICE_UUID, pay_url="https://pay.example.test/checkout"
        )

    monkeypatch.setattr(multicard_client, "create_invoice", _capture_invoice)

    resp = await client.post(
        CREATE_URL,
        headers={
            **_headers(OWNER_TELEGRAM_ID),
            "Host": "attacker.example",
            "X-Forwarded-Host": "forwarded-attacker.example",
            "X-Forwarded-Proto": "http",
        },
        json={
            "order_id": order_id,
            "return_url": "https://app.biotact.uz/payment/success?order_id=ORD-P01&id=1",
        },
    )

    assert resp.status_code == 200, resp.text
    assert len(captured) == 1
    assert captured[0].callback_url == f"{CALLBACK_ORIGIN}/api/v1/payments/callback"


@pytest.mark.parametrize(
    ("callback_origin", "miniapp_origin"),
    [
        ("", MINIAPP_ORIGIN),
        ("http://callback.example.test", MINIAPP_ORIGIN),
        ("https://callback.example.test/", MINIAPP_ORIGIN),
        (CALLBACK_ORIGIN, ""),
        (CALLBACK_ORIGIN, "http://app.example.test"),
        (CALLBACK_ORIGIN, "https://app.example.test/"),
        # Values that urlsplit cannot parse: the route must refuse them, not raise.
        ("https://callback.example.test:abc", MINIAPP_ORIGIN),
        (CALLBACK_ORIGIN, "https://app.example.test:99999"),
        (CALLBACK_ORIGIN, "https://[::1"),
    ],
    ids=[
        "callback-empty",
        "callback-http",
        "callback-trailing-slash",
        "miniapp-empty",
        "miniapp-http",
        "miniapp-trailing-slash",
        "callback-non-numeric-port",
        "miniapp-port-out-of-range",
        "miniapp-unclosed-ipv6",
    ],
)
async def test_p02_invalid_payment_or_miniapp_origin_returns_503(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    sent_messages: list[str],
    caplog: pytest.LogCaptureFixture,
    callback_origin: str,
    miniapp_origin: str,
) -> None:
    """P-02: invalid payment_callback_base_url or miniapp_public_origin -> 503 and warning log."""
    order_id = await _seed_order(maker, order_number="ORD-P02", with_item=True)
    monkeypatch.setattr(settings, "payment_callback_base_url", callback_origin)
    _set_miniapp_origin(monkeypatch, miniapp_origin)
    create_invoice = AsyncMock(
        return_value=MultiCardPaymentResponse(
            uuid=INVOICE_UUID, pay_url="https://pay.example.test/checkout"
        )
    )
    monkeypatch.setattr(multicard_client, "create_invoice", create_invoice)
    caplog.set_level(logging.WARNING)

    resp = await client.post(
        CREATE_URL,
        headers=_headers(OWNER_TELEGRAM_ID),
        json={"order_id": order_id, "return_url": f"{MINIAPP_ORIGIN}/payment/success"},
    )

    assert resp.status_code == 503
    assert resp.json() == {"detail": {"code": "payment_unconfigured"}}
    create_invoice.assert_not_awaited()
    order = await _committed_order(maker, order_id)
    assert order.payment_uuid is None
    assert sent_messages == []

    warnings = [
        rec.getMessage()
        for rec in caplog.records
        if rec.levelno == logging.WARNING and "payment_unconfigured" in rec.getMessage()
    ]
    assert len(warnings) == 1
    for non_empty_setting in (callback_origin, miniapp_origin):
        if non_empty_setting:
            assert non_empty_setting not in warnings[0]


@pytest.mark.parametrize(
    "bad_return_url",
    [
        "https://evil.example/x",
        "https://app.example.test.evil.example/",
        "https://app.example.test@evil.example/",
        "http://app.example.test/x",
        "//evil.example/x",
        "javascript:alert(1)",
        "https://app.example.test/x y",
        "https://app.example.test/x\ny",
        "https://app.example.test?order_id=1",
        "",
    ],
    ids=[
        "foreign-origin",
        "subdomain-prefix",
        "userinfo",
        "plain-http",
        "scheme-relative",
        "javascript",
        "space",
        "newline",
        "query-without-slash",
        "empty",
    ],
)
async def test_p03_refused_return_url_returns_422_without_side_effects(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    sent_messages: list[str],
    caplog: pytest.LogCaptureFixture,
    bad_return_url: str,
) -> None:
    """P-03: return_url outside miniapp_public_origin or printable non-space ASCII -> 422."""
    order_id = await _seed_order(maker, order_number="ORD-P03-BAD", with_item=True)
    monkeypatch.setattr(settings, "payment_callback_base_url", CALLBACK_ORIGIN)
    _set_miniapp_origin(monkeypatch, MINIAPP_ORIGIN)
    create_invoice = AsyncMock(
        return_value=MultiCardPaymentResponse(
            uuid=INVOICE_UUID, pay_url="https://pay.example.test/checkout"
        )
    )
    monkeypatch.setattr(multicard_client, "create_invoice", create_invoice)
    caplog.set_level(logging.WARNING)

    resp = await client.post(
        CREATE_URL,
        headers=_headers(OWNER_TELEGRAM_ID),
        json={"order_id": order_id, "return_url": bad_return_url},
    )

    assert resp.status_code == 422
    assert resp.json() == {"detail": {"code": "return_url_not_allowed"}}
    create_invoice.assert_not_awaited()
    order = await _committed_order(maker, order_id)
    assert order.payment_uuid is None
    assert order.payment_status == "unpaid"
    assert sent_messages == []

    warnings = [
        rec.getMessage()
        for rec in caplog.records
        if rec.levelno == logging.WARNING and "return_url_not_allowed" in rec.getMessage()
    ]
    assert len(warnings) == 1


@pytest.mark.parametrize(
    "allowed_return_url",
    [
        f"{MINIAPP_ORIGIN}/payment/success?order_id=ORD-P03-OK&id=1",
        MINIAPP_ORIGIN,
    ],
    ids=["with-path-and-query", "exact-origin"],
)
async def test_p03_allowed_return_url_reaches_multicard_unchanged(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    allowed_return_url: str,
) -> None:
    """P-03: allowed return_url values pass through unchanged to Multicard."""
    order_id = await _seed_order(maker, order_number="ORD-P03-OK", with_item=True)
    monkeypatch.setattr(settings, "payment_callback_base_url", CALLBACK_ORIGIN)
    _set_miniapp_origin(monkeypatch, MINIAPP_ORIGIN)

    captured: list[PaymentCreateData] = []

    async def _capture_invoice(data: PaymentCreateData) -> MultiCardPaymentResponse:
        captured.append(data)
        return MultiCardPaymentResponse(
            uuid=INVOICE_UUID, pay_url="https://pay.example.test/checkout"
        )

    monkeypatch.setattr(multicard_client, "create_invoice", _capture_invoice)

    resp = await client.post(
        CREATE_URL,
        headers=_headers(OWNER_TELEGRAM_ID),
        json={"order_id": order_id, "return_url": allowed_return_url},
    )

    assert resp.status_code == 200, resp.text
    assert len(captured) == 1
    assert captured[0].return_url == allowed_return_url
    assert captured[0].callback_url == f"{CALLBACK_ORIGIN}/api/v1/payments/callback"


async def test_p04_check_order_and_stock_managed_refusal_side_effects(
    client: AsyncClient,
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    sent_messages: list[str],
) -> None:
    """P-04: 404 -> 403 -> P-02 -> P-03 -> existing flow, before stock-owner or paid checks."""
    sized_id = await _seed_order(maker, order_number="ORD-P04-SIZED", sized=True)
    paid_id = await _seed_order(
        maker,
        order_number="ORD-P04-PAID",
        payment_uuid=INVOICE_UUID,
        payment_status="paid",
        order_status=OrderStatus.CONFIRMED,
        paid_at=PAID_AT,
        with_item=True,
    )
    create_invoice = AsyncMock(
        return_value=MultiCardPaymentResponse(
            uuid=OTHER_UUID, pay_url="https://pay.example.test/checkout"
        )
    )
    monkeypatch.setattr(multicard_client, "create_invoice", create_invoice)

    # 1 & 2: 404 and 403 precede P-02 and P-03 even when settings and return_url are both invalid.
    monkeypatch.setattr(settings, "payment_callback_base_url", "")
    _set_miniapp_origin(monkeypatch, MINIAPP_ORIGIN)

    missing = await client.post(
        CREATE_URL,
        headers=_headers(OWNER_TELEGRAM_ID),
        json={"order_id": 999_999, "return_url": "https://evil.example/x"},
    )
    assert missing.status_code == 404

    forbidden = await client.post(
        CREATE_URL,
        headers=_headers(STRANGER_TELEGRAM_ID),
        json={"order_id": sized_id, "return_url": "https://evil.example/x"},
    )
    assert forbidden.status_code == 403

    # 3: P-02 (503) precedes size_stock.lock_order / create_owned_invoice on stock-managed order.
    unconfigured = await client.post(
        CREATE_URL,
        headers=_headers(OWNER_TELEGRAM_ID),
        json={"order_id": sized_id, "return_url": f"{MINIAPP_ORIGIN}/payment/success"},
    )
    assert unconfigured.status_code == 503
    assert unconfigured.json() == {"detail": {"code": "payment_unconfigured"}}

    # 4: With valid settings, P-03 (422) precedes create_owned_invoice on stock-managed order
    # and precedes the 400 already-paid check on an ordinary paid order.
    monkeypatch.setattr(settings, "payment_callback_base_url", CALLBACK_ORIGIN)

    bad_sized = await client.post(
        CREATE_URL,
        headers=_headers(OWNER_TELEGRAM_ID),
        json={"order_id": sized_id, "return_url": "https://evil.example/x"},
    )
    assert bad_sized.status_code == 422
    assert bad_sized.json() == {"detail": {"code": "return_url_not_allowed"}}

    sized_order = await _committed_order(maker, sized_id)
    assert sized_order.size_owner_state == "not_started"
    assert sized_order.creation_token is None
    assert sized_order.size_revision == 0
    assert sized_order.owned_invoice_uuid is None
    assert await _size_record_count(maker, sized_id) == 0

    bad_paid = await client.post(
        CREATE_URL,
        headers=_headers(OWNER_TELEGRAM_ID),
        json={"order_id": paid_id, "return_url": "https://evil.example/x"},
    )
    assert bad_paid.status_code == 422
    assert bad_paid.json() == {"detail": {"code": "return_url_not_allowed"}}

    create_invoice.assert_not_awaited()
    assert sent_messages == []
