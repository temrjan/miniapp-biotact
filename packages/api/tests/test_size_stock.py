"""Finite-size contract against PostgreSQL; external payment/notification boundaries are doubles."""

import asyncio
import hashlib
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.database import get_db
from src.loyalty.models import CustomerProfile, LedgerReason, PointLedger
from src.loyalty.service import get_balance, get_loyalty_summary
from src.main import app
from src.notifications.service import telegram_notifier
from src.orders.models import Order, OrderStatus
from src.orders.schemas import OrderCreate, OrderItemCreate
from src.orders.service import (
    InvalidRedeemAmountError,
    build_quote,
    create_order,
    expire_stale_orders,
    expire_stale_partials,
)
from src.payments import size_stock as owner
from src.payments.schemas import (
    MultiCardPaymentResponse,
    PaymentCreateData,
    SuccessCallbackData,
    WebhookData,
)
from src.payments.service import MultiCardClient, build_ofd_items, multicard_client
from src.products.models import Product, ProductSize, SizeStockRecord
from src.products.operations import (
    initialize_vest,
    inspect_order,
    inspect_vest,
    read_evidence,
    recover_order,
)
from src.users.models import User
from tests.conftest import TEST_DATABASE_URL, _make_init_data

BOT_TOKEN = "isolated-size-stock-bot"
HEADERS = {"X-Init-Data": _make_init_data(BOT_TOKEN, {"id": 39001, "first_name": "Buyer"})}


@pytest_asyncio.fixture
async def maker(test_db: AsyncSession) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(TEST_DATABASE_URL)
    sm = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        async with sm() as db, db.begin():
            db.add_all(
                [
                    User(id=1, telegram_id=39001, first_name="Buyer", is_admin=True),
                    Product(
                        id=39,
                        name="Жилетка двусторонняя Healthy Lifestyle",
                        slug="zhiletka-healthy-lifestyle",
                        price=Decimal("30000"),
                    ),
                    Product(id=40, name="Ordinary", slug="ordinary", price=Decimal("10000")),
                ]
            )
            await db.flush()
            db.add(CustomerProfile(user_id=1))
            db.add(PointLedger(user_id=1, amount=100000, reason=LedgerReason.CASHBACK))
            db.add_all(
                [
                    ProductSize(product_id=39, size=label, available_quantity=50)
                    for label in ("L", "XL", "2XL")
                ]
            )
        yield sm
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def external_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "telegram_bot_token", BOT_TOKEN)
    monkeypatch.setattr(settings, "multicard_store_id", "6")
    monkeypatch.setattr(settings, "payment_callback_base_url", "https://callback.example")
    monkeypatch.setattr(settings, "miniapp_public_origin", "https://shop.example")
    monkeypatch.setattr(telegram_notifier, "notify_order_created", AsyncMock())
    monkeypatch.setattr(telegram_notifier, "notify_payment_success", AsyncMock())
    monkeypatch.setattr(
        multicard_client,
        "create_invoice",
        AsyncMock(side_effect=AssertionError("Unexpected outgoing invoice creation")),
    )
    monkeypatch.setattr(multicard_client, "delete_owned_invoice", AsyncMock(return_value=False))
    monkeypatch.setattr(multicard_client, "get_invoice_status", AsyncMock(return_value=None))


@pytest_asyncio.fixture
async def http(maker: async_sessionmaker[AsyncSession]) -> AsyncGenerator[AsyncClient, None]:
    async def request_db() -> AsyncGenerator[AsyncSession, None]:
        async with maker() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    app.dependency_overrides[get_db] = request_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


def request(*sizes: str | None, points: int = 0) -> OrderCreate:
    return OrderCreate(
        items=[OrderItemCreate(product_id=39, quantity=1, size=size) for size in sizes],
        points_to_apply=points,
    )


async def admit(sm: async_sessionmaker[AsyncSession], data: OrderCreate) -> int:
    async with sm() as db, db.begin():
        order = await create_order(db, data, 1)
        return order.id


def payment_data(order: Order) -> PaymentCreateData:
    return PaymentCreateData(
        store_id="6",
        amount=int((order.total - order.points_applied) * 100),
        invoice_id=order.order_number,
        callback_url="http://test/callback",
        return_url="http://test/return",
        ofd=[],
    )


async def start_invoice(
    sm: async_sessionmaker[AsyncSession], oid: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        multicard_client,
        "create_invoice",
        AsyncMock(
            return_value=MultiCardPaymentResponse(
                uuid="owned-invoice", pay_url="https://payment.example/owned"
            )
        ),
    )
    async with sm() as db:
        order = await db.get(Order, oid)
        assert order is not None
        await owner.create_owned_invoice(db, oid, payment_data(order))


def callback(order: Order, invoice_uuid: str = "owned-invoice") -> SuccessCallbackData:
    return SuccessCallbackData(
        store_id=6,
        amount=int((order.total - order.points_applied) * 100),
        invoice_id=order.order_number,
        uuid=invoice_uuid,
        sign="test",
    )


async def quantities(sm: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    async with sm() as db:
        rows = (
            (await db.execute(select(ProductSize).where(ProductSize.product_id == 39)))
            .scalars()
            .all()
        )
        return {row.size: row.available_quantity for row in rows}


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/products/39",
        "/api/v1/products/39?lang=uz",
        "/api/v1/products/slug/zhiletka-healthy-lifestyle",
        "/api/v1/products/39?include_all_translations=true",
    ],
)
async def test_public_size_inventory_has_labels_but_no_counts(http: AsyncClient, path: str) -> None:
    response = await http.get(path)
    assert response.status_code == 200, response.text
    product = response.json()
    assert product["stock_quantity"] is None
    assert product["available_sizes"] == ["L", "XL", "2XL"]
    assert product["requires_size"] is True
    assert product["is_in_stock"] is True
    assert not {"sizes", "available_quantity", "revision", "size_stock"} & product.keys()


@pytest.mark.parametrize(
    ("size", "code"), [(None, "size_required"), ("l", "invalid_size"), ("M", "invalid_size")]
)
async def test_refusal_leaves_no_order_or_stock_effect(
    http: AsyncClient, maker: async_sessionmaker[AsyncSession], size: str | None, code: str
) -> None:
    response = await http.post(
        "/api/v1/orders",
        headers=HEADERS,
        json={"items": [{"product_id": 39, "quantity": 1, "size": size}]},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == {"code": code, "product_id": 39, "size": size}
    assert await quantities(maker) == {"L": 50, "XL": 50, "2XL": 50}
    async with maker() as db:
        assert await db.scalar(select(func.count()).select_from(Order)) == 0
    multicard_client.create_invoice.assert_not_awaited()
    telegram_notifier.notify_order_created.assert_not_awaited()


async def test_quote_size_and_stock_fingerprints_and_duplicate_demand(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as db:
        first = await build_quote(db, request("L"), 1)
        other = await build_quote(db, request("XL"), 1)
        assert first.quote_fingerprint != other.quote_fingerprint
        row = await db.scalar(select(ProductSize).where(ProductSize.size == "L"))
        assert row is not None
        row.available_quantity = 1
        await db.commit()
        fresh = await build_quote(db, request("L"), 1)
        assert first.quote_fingerprint == fresh.quote_fingerprint
        with pytest.raises(HTTPException) as error:
            await create_order(db, request("XL", "L", "L", points=5000), 1)
        assert error.value.status_code == 409
        assert error.value.detail == {"code": "size_unavailable", "product_id": 39, "size": "L"}
        await db.rollback()
    assert await quantities(maker) == {"L": 1, "XL": 50, "2XL": 50}


async def test_two_real_sessions_compete_for_last_unit(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as db, db.begin():
        row = await db.scalar(select(ProductSize).where(ProductSize.size == "L"))
        assert row is not None
        row.available_quantity = 1

    async def buy() -> int:
        try:
            await admit(maker, request("L", points=5000))
            return 201
        except HTTPException as exc:
            return exc.status_code

    assert sorted(await asyncio.gather(buy(), buy())) == [201, 409]
    assert (await quantities(maker))["L"] == 0
    async with maker() as db:
        assert await db.scalar(select(func.count()).select_from(Order)) == 1
        assert await get_balance(db, 1) == 100000


@pytest.mark.parametrize(("points", "paid"), [(0, False), (5000, False), (30000, True)])
async def test_card_mixed_and_points_orders_allocate_once(
    maker: async_sessionmaker[AsyncSession], points: int, paid: bool
) -> None:
    oid = await admit(maker, request("XL", points=points))
    async with maker() as db:
        order = await db.get(Order, oid)
        assert order is not None
        assert order.size_owner_state == ("paid" if paid else "not_started")
        assert order.items[0].size == "XL"
        assert order.items[0].allocated_quantity == 1
        assert order.total == Decimal("30000")
        assert await get_balance(db, 1) == (70000 if paid else 100000)
    assert await quantities(maker) == {"L": 50, "XL": 49, "2XL": 50}
    multicard_client.create_invoice.assert_not_awaited()


async def test_one_invoice_concurrent_create_and_reopen(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    oid = await admit(maker, request("L"))
    entered, finish = asyncio.Event(), asyncio.Event()

    async def post(_: PaymentCreateData) -> MultiCardPaymentResponse:
        entered.set()
        await finish.wait()
        return MultiCardPaymentResponse(
            uuid="owned-invoice", pay_url="https://payment.example/owned"
        )

    external = AsyncMock(side_effect=post)
    monkeypatch.setattr(multicard_client, "create_invoice", external)

    async def create() -> tuple[object, bool]:
        async with maker() as db:
            order = await db.get(Order, oid)
            assert order is not None
            return await owner.create_owned_invoice(db, oid, payment_data(order))

    task = asyncio.create_task(create())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(HTTPException) as error:
            await create()
        assert error.value.detail == {"code": "payment_processing", "order_id": oid}
        async with maker() as db:
            assert await owner.cancel_owned_order(db, oid) is False
        assert (await quantities(maker))["L"] == 49
        finish.set()
        response, notify = await asyncio.wait_for(task, 5)
        repeat, repeat_notify = await create()
        assert repeat == response
        assert notify is True
        assert repeat_notify is False
        external.assert_awaited_once()
    finally:
        finish.set()
        await task


async def test_cancel_wins_before_creation_and_unknown_post_stays_held(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    cancelled = await admit(maker, request("L"))
    async with maker() as db:
        assert await owner.cancel_owned_order(db, cancelled) is True
        order = await db.get(Order, cancelled)
        assert order is not None
        with pytest.raises(HTTPException) as error:
            await owner.create_owned_invoice(db, cancelled, payment_data(order))
        assert error.value.detail == {"code": "order_payment_closed", "order_id": cancelled}
    external = AsyncMock(side_effect=TimeoutError("Result lost"))
    monkeypatch.setattr(multicard_client, "create_invoice", external)
    oid = await admit(maker, request("XL", points=5000))
    async with maker() as db:
        order = await db.get(Order, oid)
        assert order is not None
        with pytest.raises(HTTPException) as error:
            await owner.create_owned_invoice(db, oid, payment_data(order))
        assert error.value.status_code == 502
    async with maker() as db:
        order = await db.get(Order, oid)
        assert order is not None
        assert order.size_owner_state == "uncertain"
        with pytest.raises(HTTPException):
            await owner.create_owned_invoice(db, oid, payment_data(order))
        assert await owner.cancel_owned_order(db, oid) is False
    external.assert_awaited_once()
    assert await quantities(maker) == {"L": 50, "XL": 49, "2XL": 50}


async def test_untrusted_hint_does_not_block_verified_success(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    oid = await admit(maker, request("L", points=5000))
    await start_invoice(maker, oid, monkeypatch)
    async with maker() as db:
        order = await db.get(Order, oid)
        assert order is not None
        event = WebhookData(
            uuid="owned-invoice",
            invoice_id=order.order_number,
            amount=2500000,
            status="success",
            sign="test",
        )
        await owner.lifecycle_hint(db, oid, event)
        await owner.lifecycle_hint(db, oid, event)
        assert order.accepted_settlement is False
        assert await owner.success_callback(db, order, callback(order)) == (True, True)
        assert await owner.success_callback(db, order, callback(order)) == (True, False)
        assert await get_balance(db, 1) == 95000
        records = (
            (
                await db.execute(
                    select(SizeStockRecord).where(
                        SizeStockRecord.action == "reconciliation_pending"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(records) == 1
    assert (await quantities(maker))["L"] == 49


async def test_early_success_requires_identity_and_cannot_be_downgraded(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    oid = await admit(maker, request("L"))

    async def post(data: PaymentCreateData) -> MultiCardPaymentResponse:
        async with maker() as db:
            order = await db.get(Order, oid)
            assert order is not None
            with pytest.raises(HTTPException) as error:
                await owner.success_callback(db, order, callback(order))
            assert error.value.status_code == 500
        monkeypatch.setattr(
            multicard_client,
            "get_invoice_status",
            AsyncMock(
                return_value={
                    "uuid": "owned-invoice",
                    "invoice_id": data.invoice_id,
                    "store_id": 6,
                    "amount": 3000000,
                }
            ),
        )
        async with maker() as db:
            order = await db.get(Order, oid)
            assert order is not None
            assert await owner.success_callback(db, order, callback(order)) == (True, True)
        return MultiCardPaymentResponse(
            uuid="owned-invoice", pay_url="https://payment.example/owned"
        )

    monkeypatch.setattr(multicard_client, "create_invoice", AsyncMock(side_effect=post))
    async with maker() as db:
        order = await db.get(Order, oid)
        assert order is not None
        await owner.create_owned_invoice(db, oid, payment_data(order))
    async with maker() as db:
        order = await db.get(Order, oid)
        assert order is not None
        assert order.size_owner_state == "paid"
        assert order.payment_status == "paid"
        assert order.checkout_url == "https://payment.example/owned"


async def test_success_wins_while_delete_is_in_flight(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    oid = await admit(maker, request("L", points=5000))
    await start_invoice(maker, oid, monkeypatch)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def delete(_: str) -> bool:
        entered.set()
        await finish.wait()
        return True

    monkeypatch.setattr(multicard_client, "delete_owned_invoice", AsyncMock(side_effect=delete))

    async def cancel() -> bool:
        async with maker() as db:
            return await owner.cancel_owned_order(db, oid)

    task = asyncio.create_task(cancel())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        async with maker() as db:
            order = await db.get(Order, oid)
            assert order is not None
            assert await owner.success_callback(db, order, callback(order)) == (True, True)
        finish.set()
        assert await asyncio.wait_for(task, 5) is False
    finally:
        finish.set()
        await task
    assert (await quantities(maker))["L"] == 49
    async with maker() as db:
        assert await get_balance(db, 1) == 95000


async def test_confirmed_delete_restores_once_and_callback_reports_closed(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    oid = await admit(maker, request("L"))
    await start_invoice(maker, oid, monkeypatch)
    monkeypatch.setattr(multicard_client, "delete_owned_invoice", AsyncMock(return_value=True))
    async with maker() as db:
        assert await owner.cancel_owned_order(db, oid) is True
        assert await owner.cancel_owned_order(db, oid) is True
        order = await db.get(Order, oid)
        assert order is not None
        assert await owner.success_callback(db, order, callback(order)) == (False, False)
        with pytest.raises(HTTPException) as error:
            await owner.change_status(db, oid, OrderStatus.SHIPPED)
        assert error.value.detail == {"code": "order_state_conflict"}
    assert (await quantities(maker))["L"] == 50
    multicard_client.delete_owned_invoice.assert_awaited_once()


async def test_uncertain_claim_commit_acknowledgment_cannot_send_post(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    oid = await admit(maker, request("L", points=5000))
    async with maker() as db:
        order = await db.get(Order, oid)
        assert order is not None
        original_commit = db.commit

        async def lost_acknowledgment() -> None:
            await original_commit()
            raise TimeoutError("Commit acknowledgment lost")

        monkeypatch.setattr(db, "commit", lost_acknowledgment)
        with pytest.raises(TimeoutError, match="acknowledgment"):
            await owner.create_owned_invoice(db, oid, payment_data(order))
    async with maker() as db:
        order = await db.get(Order, oid)
        assert order is not None
        assert order.creation_token
        assert order.size_owner_state == "creating"
        with pytest.raises(HTTPException) as error:
            await owner.create_owned_invoice(db, oid, payment_data(order))
        assert error.value.status_code == 409
        assert await owner.cancel_owned_order(db, oid) is False
        assert (await get_loyalty_summary(db, 39001)).available == 95000
    assert (await quantities(maker))["L"] == 49
    multicard_client.create_invoice.assert_not_awaited()


async def test_http_invoice_reuse_callback_acceptance_and_notification_sizes(
    http: AsyncClient, maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    oid = await admit(maker, request("XL", points=5000))
    external = AsyncMock(
        return_value=MultiCardPaymentResponse(
            uuid="owned-invoice", pay_url="https://payment.example/owned"
        )
    )
    monkeypatch.setattr(multicard_client, "create_invoice", external)
    first = await http.post(
        "/api/v1/payments/create",
        headers=HEADERS,
        json={"order_id": oid, "return_url": "https://shop.example/first"},
    )
    second = await http.post(
        "/api/v1/payments/create",
        headers=HEADERS,
        json={"order_id": oid, "return_url": "https://shop.example/second"},
    )
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    external.assert_awaited_once()
    telegram_notifier.notify_order_created.assert_awaited_once()
    assert telegram_notifier.notify_order_created.await_args.kwargs["items"][0]["size"] == "XL"
    async with maker() as db:
        order = await db.get(Order, oid)
        assert order is not None
        body = callback(order).model_dump()
    text = f"{body['store_id']}{body['invoice_id']}{body['amount']}{multicard_client.secret}"
    body["sign"] = hashlib.md5(text.encode()).hexdigest()
    for _ in range(2):
        result = await http.post("/api/v1/payments/callback", json=body)
        assert result.status_code == 200
        assert result.json() == {"success": True}
    telegram_notifier.notify_payment_success.assert_awaited_once()
    assert telegram_notifier.notify_payment_success.await_args.kwargs["items"][0]["size"] == "XL"
    async with maker() as db:
        assert await get_balance(db, 1) == 95000
    assert (await quantities(maker))["XL"] == 49


@pytest.mark.parametrize("dispatched", [False, True])
async def test_full_refund_preserves_points_and_only_restock_before_dispatch(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch, dispatched: bool
) -> None:
    oid = await admit(maker, request("L", points=5000))
    await start_invoice(maker, oid, monkeypatch)
    async with maker() as db:
        order = await db.get(Order, oid)
        assert order is not None
        assert await owner.success_callback(db, order, callback(order)) == (True, True)
        if dispatched:
            await owner.change_status(db, oid, OrderStatus.SHIPPED)
            await db.commit()
        order = await owner.lock_order(db, oid)
        amount = 2500000
        details = {
            "uuid": "owned-invoice",
            "store_id": 6,
            "invoice_id": order.order_number,
            "amount": amount,
        }
        evidence = {
            "order_id": oid,
            "expected_revision": order.size_revision,
            "creation_token": order.creation_token,
            "uuid": "owned-invoice",
            "invoice_details": details,
            "full_refund_completed": True,
            "refund_amount": amount,
        }
        revision = order.size_revision
        assert (
            await recover_order(
                db,
                order_id=oid,
                expected_revision=revision,
                action="full_refund",
                actor="operator",
                reason="Verified full refund",
                evidence_ref="authorized-record",
                evidence=evidence,
            )
            == "applied"
        )
        order = await owner.lock_order(db, oid)
        assert order.payment_status == "refunded"
        assert order.accepted_settlement
        assert (order.dispatched_at is not None) is dispatched
        assert await owner.success_callback(db, order, callback(order)) == (True, False)
        order = await owner.lock_order(db, oid)
        evidence["expected_revision"] = order.size_revision
        assert (
            await recover_order(
                db,
                order_id=oid,
                expected_revision=order.size_revision,
                action="full_refund",
                actor="operator",
                reason="Repeat refund",
                evidence_ref="authorized-record",
                evidence=evidence,
            )
            == "unchanged"
        )
        assert await get_balance(db, 1) == 95000
    assert (await quantities(maker))["L"] == (49 if dispatched else 50)


async def test_recover_bound_invoice_rejects_wrong_identity_and_preserves_url(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    oid = await admit(maker, request("L"))
    async with maker() as db:
        order = await owner.lock_order(db, oid)
        order.creation_token = "claim"
        order.size_owner_state = "uncertain"
        await db.commit()
        evidence = {
            "order_id": oid,
            "expected_revision": 0,
            "creation_token": "claim",
            "uuid": "recovered",
        }
        details = {
            "uuid": "recovered",
            "store_id": 6,
            "invoice_id": "wrong-order",
            "amount": 3000000,
            "checkout_url": "https://payment.example/original",
        }
        get = AsyncMock(return_value=details)
        monkeypatch.setattr(multicard_client, "get_invoice_status", get)
        with pytest.raises(HTTPException) as error:
            await recover_order(
                db,
                order_id=oid,
                expected_revision=0,
                action="bind",
                actor="operator",
                reason="Recover invoice",
                evidence_ref="authorized-record",
                evidence=evidence,
            )
        assert error.value.detail == {"code": "recovery_needs_evidence"}
        await db.rollback()
        order = await db.get(Order, oid)
        assert order is not None
        details["invoice_id"] = order.order_number
        assert (
            await recover_order(
                db,
                order_id=oid,
                expected_revision=0,
                action="bind",
                actor="operator",
                reason="Recover invoice",
                evidence_ref="authorized-record",
                evidence=evidence,
            )
            == "applied"
        )
        order = await owner.lock_order(db, oid)
        evidence["expected_revision"] = order.size_revision
        details["checkout_url"] = "https://payment.example/different"
        assert (
            await recover_order(
                db,
                order_id=oid,
                expected_revision=order.size_revision,
                action="bind",
                actor="operator",
                reason="Repeat binding",
                evidence_ref="authorized-record",
                evidence=evidence,
            )
            == "unchanged"
        )
        assert order.checkout_url == "https://payment.example/original"
        get.assert_awaited_with("recovered")
    assert (await quantities(maker))["L"] == 49


async def test_confirmed_annulment_crash_is_recoverable_with_same_fence(
    maker: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    oid = await admit(maker, request("L", points=5000))
    await start_invoice(maker, oid, monkeypatch)
    monkeypatch.setattr(multicard_client, "delete_owned_invoice", AsyncMock(return_value=True))
    async with maker() as db:
        original_commit = db.commit
        commits = 0

        async def interrupted_finalization() -> None:
            nonlocal commits
            commits += 1
            if commits == 2:
                raise TimeoutError("Crash before local finalization")
            await original_commit()

        monkeypatch.setattr(db, "commit", interrupted_finalization)
        with pytest.raises(TimeoutError, match="finalization"):
            await owner.cancel_owned_order(db, oid)
    assert (await quantities(maker))["L"] == 49
    async with maker() as db:
        order = await owner.lock_order(db, oid)
        assert order.size_owner_state == "cancelling"
        assert order.cancellation_token
        evidence = {
            "order_id": oid,
            "expected_revision": order.size_revision,
            "creation_token": order.creation_token,
            "uuid": order.owned_invoice_uuid,
            "invoice_details": {
                "uuid": order.owned_invoice_uuid,
                "store_id": 6,
                "invoice_id": order.order_number,
                "amount": 2500000,
            },
            "cancellation_token": order.cancellation_token,
            "annulment_completed": True,
            "no_accepted_or_future_payment": True,
        }
        assert (
            await recover_order(
                db,
                order_id=oid,
                expected_revision=order.size_revision,
                action="annulled",
                actor="operator",
                reason="Verified successful DELETE",
                evidence_ref="authorized-record",
                evidence=evidence,
            )
            == "applied"
        )
        order = await owner.lock_order(db, oid)
        assert await owner.success_callback(db, order, callback(order)) == (False, False)
        assert await get_balance(db, 1) == 100000
        assert (await get_loyalty_summary(db, 39001)).available == 100000
    assert (await quantities(maker))["L"] == 50


async def test_depleted_rows_remain_configured_and_strong_role_blocks_mutations(
    http: AsyncClient, maker: async_sessionmaker[AsyncSession], admin_headers: dict[str, str]
) -> None:
    async with maker() as db, db.begin():
        rows = (await db.execute(select(ProductSize))).scalars().all()
        for row in rows:
            row.available_quantity = 0
    for path in (
        "/api/v1/products",
        "/api/v1/products?include_all_translations=true",
        "/api/v1/products/39?lang=uz",
    ):
        response = (await http.get(path)).json()
        product = (
            next(p for p in response["items"] if p["id"] == 39) if "items" in response else response
        )
        assert product["requires_size"] is True
        assert product["available_sizes"] == []
        assert product["stock_quantity"] is None
        assert product["is_in_stock"] is False
        assert "available_quantity" not in str(product)
    inventory = (await http.get("/api/v1/admin/products/39/sizes", headers=admin_headers)).json()
    assert inventory["merchant_is_in_stock"] is True
    assert len(inventory["sizes"]) == 3
    body = {
        "sizes": [
            {"size": r["size"], "available_quantity": 1, "expected_revision": r["revision"]}
            for r in inventory["sizes"]
        ]
    }
    assert (
        await http.patch("/api/v1/admin/products/39/sizes", headers=admin_headers, json=body)
    ).status_code == 200
    oid = await admit(maker, request("XL", points=30000))
    async with maker() as db, db.begin():
        user = await db.get(User, 900001)
        assert user is not None
        user.is_admin = False
    for prefix in ("/api/v1/orders", "/api/v1/admin/orders"):
        assert (
            await http.patch(
                f"{prefix}/{oid}/status", headers=admin_headers, json={"status": "cancelled"}
            )
        ).status_code == 403
    assert (
        await http.patch("/api/v1/admin/products/39/sizes", headers=admin_headers, json=body)
    ).status_code == 403
    assert (await quantities(maker))["XL"] == 0


async def test_old_uncertain_reserve_remains_in_profile_and_all_cleanup(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    oid = await admit(maker, request("XL", points=5000))
    async with maker() as db:
        order = await db.get(Order, oid)
        assert order is not None
        order.size_owner_state = "uncertain"
        order.creation_token = "lost-creation"
        order.status = OrderStatus.PROCESSING
        order.payment_status = "failed"
        order.created_at = datetime.now(UTC) - timedelta(days=2)
        await db.commit()
        user = await db.get(User, 1)
        assert user is not None
        summary = await get_loyalty_summary(db, user.telegram_id)
        assert summary.available == 95000
        assert summary.reserved_until is None
        assert (await expire_stale_partials(db, user_id=1)).blocked_orders == 1
        assert (await expire_stale_orders(db)).blocked_orders == 1
    assert (await quantities(maker))["XL"] == 49


async def test_stale_admin_quantity_save_cannot_erase_a_sale(
    http: AsyncClient, maker: async_sessionmaker[AsyncSession], admin_headers: dict[str, str]
) -> None:
    response = await http.get("/api/v1/admin/products/39/sizes", headers=admin_headers)
    before = response.json()
    assert response.status_code == 200
    await admit(maker, request("L"))
    payload = {
        "sizes": [
            {"size": row["size"], "available_quantity": 50, "expected_revision": row["revision"]}
            for row in before["sizes"]
        ]
    }
    response = await http.patch(
        "/api/v1/admin/products/39/sizes", headers=admin_headers, json=payload
    )
    assert response.status_code == 409
    assert response.json()["detail"] == {"code": "size_stock_conflict"}
    assert await quantities(maker) == {"L": 49, "XL": 50, "2XL": 50}
    assert (await http.get("/api/v1/admin/products/39/sizes")).status_code in (401, 403)
    for invalid in (True, -1, 0.5, 2147483648):
        payload["sizes"][0]["available_quantity"] = invalid
        assert (
            await http.patch("/api/v1/admin/products/39/sizes", headers=admin_headers, json=payload)
        ).status_code == 422
    response = await http.patch(
        "/api/v1/products/39",
        headers=admin_headers,
        json={"stock_quantity": 50, "name": "Must not change"},
    )
    assert response.status_code == 422
    product = (await http.get("/api/v1/products/39")).json()
    assert product["name"] == "Жилетка двусторонняя Healthy Lifestyle"


async def test_paid_cancel_and_physical_return_effects(
    http: AsyncClient, maker: async_sessionmaker[AsyncSession], admin_headers: dict[str, str]
) -> None:
    first = await admit(maker, request("L", points=30000))
    for _ in range(2):
        response = await http.patch(
            f"/api/v1/admin/orders/{first}/status",
            headers=admin_headers,
            json={"status": "cancelled"},
        )
        assert response.status_code == 200
        assert response.json()["payment_status"] == "paid"
    assert (await quantities(maker))["L"] == 50
    second = await admit(maker, request("XL", "XL", points=60000))
    response = await http.patch(
        f"/api/v1/orders/{second}/status", headers=admin_headers, json={"status": "shipped"}
    )
    assert response.status_code == 200
    response = await http.patch(
        f"/api/v1/admin/orders/{second}/status", headers=admin_headers, json={"status": "cancelled"}
    )
    assert response.status_code == 200
    stock = response.json()["size_stock"]
    assert stock["dispatched_at"] is not None
    assert (await quantities(maker))["XL"] == 48
    body = {
        "expected_revision": stock["revision"],
        "reason": "One saleable unit received",
        "items": [{"order_item_id": stock["lines"][0]["order_item_id"], "quantity": 1}],
    }
    response = await http.post(
        f"/api/v1/admin/orders/{second}/size-returns", headers=admin_headers, json=body
    )
    assert response.status_code == 200
    assert response.json()["lines"][0]["restored_quantity"] == 1
    assert (
        await http.post(
            f"/api/v1/admin/orders/{second}/size-returns", headers=admin_headers, json=body
        )
    ).status_code == 409
    assert (await quantities(maker))["XL"] == 49
    buyer = (await http.get(f"/api/v1/orders/{second}", headers=HEADERS)).json()
    assert "size_stock" not in buyer
    assert not {"allocated_quantity", "restored_quantity", "size_id"} & buyer["items"][0].keys()


async def test_fiscal_size_survives_truncation_and_notifications_escape() -> None:
    from src.orders.models import OrderItem

    item = OrderItem(
        product_name="<&" + "a" * 254,
        product_price=Decimal("30000"),
        quantity=3,
        total=Decimal("90000"),
        size="2XL",
    )
    item.product = None
    lines = build_ofd_items([item], amount_tiyins=8500000)
    assert sum(line.qty * line.price for line in lines) == 8500000
    assert all(line.name.endswith(" (2XL)") and len(line.name) <= 256 for line in lines)
    message = telegram_notifier._format_items(
        [{"product_name": "<&", "size": "2XL", "quantity": 1, "product_price": 30000}]
    )
    assert "&lt;&amp; (2XL)" in message


async def test_inspection_is_inert_and_cutover_is_once(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    # Simulate a pre-activation product using this disposable fixture only.
    async with maker() as db, db.begin():
        rows = (await db.execute(select(ProductSize))).scalars().all()
        for row in rows:
            await db.delete(row)
    async with maker() as db:
        state = await inspect_vest(db)
        assert state["sizes"] == []
        evidence = {
            "resolved_order_ids": [],
            "resolved_creation_tokens": [],
            "all_legacy_attempts_resolved": True,
            "free_units_each_size": 50,
            "original_creators_and_upstream_fenced": True,
        }
        with pytest.raises(HTTPException):
            await initialize_vest(
                db,
                action="initialize",
                actor="operator",
                reason="Cutover",
                evidence_ref="isolated-evidence",
                evidence=evidence,
            )
        await db.rollback()
        assert (
            await initialize_vest(
                db,
                action="pause",
                actor="operator",
                reason="Cutover",
                evidence_ref="isolated-evidence",
                evidence={},
            )
            == "applied"
        )
        with pytest.raises(HTTPException):
            await create_order(db, request(None), 1)
        await db.rollback()
        assert (
            await initialize_vest(
                db,
                action="initialize",
                actor="operator",
                reason="Cutover",
                evidence_ref="isolated-evidence",
                evidence=evidence,
            )
            == "applied"
        )
    await admit(maker, request("L"))
    async with maker() as db:
        assert (
            await initialize_vest(
                db,
                action="initialize",
                actor="operator",
                reason="Repeat",
                evidence_ref="isolated-evidence",
                evidence={},
            )
            == "already_initialized"
        )
    assert (await quantities(maker))["L"] == 49
    multicard_client.get_invoice_status.assert_not_awaited()


async def test_recovery_unknown_requires_independent_absence_and_creator_fence(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    oid = await admit(maker, request("L"))
    async with maker() as db:
        order = await db.get(Order, oid)
        assert order is not None
        order.creation_token = "original-token"
        order.size_owner_state = "creating"
        await db.commit()
        state = await inspect_order(db, oid)
        assert state["creation_token"] == "original-token"
        evidence = {
            "order_id": oid,
            "expected_revision": 0,
            "creation_token": "original-token",
            "provider_no_invoice_verified": True,
        }
        with pytest.raises(HTTPException) as error:
            await recover_order(
                db,
                order_id=oid,
                expected_revision=0,
                action="no_invoice",
                actor="operator",
                reason="Verified absence",
                evidence_ref="record",
                evidence=evidence,
            )
        assert error.value.detail == {"code": "recovery_needs_evidence"}
        await db.rollback()
        evidence["original_creators_and_upstream_fenced"] = True
        assert (
            await recover_order(
                db,
                order_id=oid,
                expected_revision=0,
                action="no_invoice",
                actor="operator",
                reason="Verified absence",
                evidence_ref="record",
                evidence=evidence,
            )
            == "applied"
        )
    assert (await quantities(maker))["L"] == 50


async def test_opposite_size_line_order_uses_consistent_locks(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    ready = asyncio.Barrier(2)

    async def compete(*labels: str) -> int:
        await ready.wait()
        return await admit(maker, request(*labels))

    ids = await asyncio.wait_for(asyncio.gather(compete("L", "XL"), compete("XL", "L")), timeout=10)
    assert len(set(ids)) == 2
    assert await quantities(maker) == {"L": 48, "XL": 48, "2XL": 50}


async def test_cutover_inspects_inflight_legacy_claim_and_refuses_partial_rows(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    async with maker() as db, db.begin():
        for row in (await db.execute(select(ProductSize))).scalars().all():
            await db.delete(row)
    oid = await admit(maker, request(None))
    async with maker() as db:
        token = await owner.claim_legacy_vest(db, oid)
        await initialize_vest(
            db,
            action="pause",
            actor="operator",
            reason="Cutover",
            evidence_ref="synthetic",
            evidence={},
        )
        state = await inspect_vest(db)
        assert state["unresolved_creation_tokens"] == [token]
        evidence = {
            "resolved_order_ids": [oid],
            "resolved_creation_tokens": [],
            "all_legacy_attempts_resolved": True,
            "free_units_each_size": 50,
            "original_creators_and_upstream_fenced": True,
        }
        with pytest.raises(HTTPException) as error:
            await initialize_vest(
                db,
                action="initialize",
                actor="operator",
                reason="Cutover",
                evidence_ref="synthetic",
                evidence=evidence,
            )
        assert error.value.detail == {"code": "legacy_commitments_unresolved"}
        await db.rollback()
        with pytest.raises(HTTPException):
            await owner.claim_legacy_vest(db, oid)
        await db.rollback()
        db.add(ProductSize(product_id=39, size="L", available_quantity=7))
        await db.commit()
        evidence["resolved_creation_tokens"] = [token]
        with pytest.raises(HTTPException) as error:
            await initialize_vest(
                db,
                action="initialize",
                actor="operator",
                reason="Cutover",
                evidence_ref="synthetic",
                evidence=evidence,
            )
        assert error.value.detail == {"code": "size_configuration_conflict"}
    assert await quantities(maker) == {"L": 7}
    multicard_client.create_invoice.assert_not_awaited()


async def test_sweep_cli_dry_run_keeps_unknown_order_and_all_effects_inert(
    maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import expire_stale_orders as sweep_cli

    oid = await admit(maker, request("L", points=5000))
    async with maker() as db, db.begin():
        order = await db.get(Order, oid)
        assert order is not None
        order.creation_token = "unknown-creator"
        order.size_owner_state = "uncertain"
        order.created_at = datetime.now(UTC) - timedelta(hours=1)
    monkeypatch.setattr("sys.argv", ["expire_stale_orders.py", "--dry-run"])
    assert await sweep_cli.main() == 0
    assert "1 order(s)" in capsys.readouterr().out
    assert (await quantities(maker))["L"] == 49
    async with maker() as db:
        order = await db.get(Order, oid)
        assert order is not None
        assert order.size_owner_state == "uncertain"
        assert order.creation_token == "unknown-creator"
        assert await get_balance(db, 1) == 100000
        assert await db.scalar(select(func.count()).select_from(SizeStockRecord)) == 0
    multicard_client.create_invoice.assert_not_awaited()
    multicard_client.delete_owned_invoice.assert_not_awaited()
    multicard_client.get_invoice_status.assert_not_awaited()
    telegram_notifier.notify_order_created.assert_not_awaited()
    telegram_notifier.notify_payment_success.assert_not_awaited()


def test_operator_evidence_requires_matching_authorized_protected_file(tmp_path: Path) -> None:
    path = tmp_path / "evidence.json"
    data = {
        "kind": "vest-size-stock-authorized-record-v1",
        "authorized": True,
        "authorized_by": "Captain",
        "source_ref": "synthetic-provider-record",
        "actor": "operator",
        "action": "no_invoice",
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(0o600)
    assert read_evidence(path, actor="operator", action="no_invoice") == data
    with pytest.raises(ValueError, match="matching actor"):
        read_evidence(path, actor="other", action="no_invoice")
    path.chmod(0o666)
    with pytest.raises(ValueError, match="without group/world writes"):
        read_evidence(path, actor="operator", action="no_invoice")
    path.chmod(0o600)
    data["authorized"] = False
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="authorization"):
        read_evidence(path, actor="operator", action="no_invoice")


@pytest.mark.parametrize(
    ("status", "body", "confirmed"),
    [
        (200, b'{"success":true}', True),
        (200, b'{"success":false}', False),
        (200, b'{"success":1}', False),
        (200, b"not-json", False),
        (204, b"", False),
        (404, b'{"success":true}', False),
    ],
)
async def test_owned_annulment_requires_exact_success_response(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    body: bytes,
    confirmed: bool,
) -> None:
    client = MultiCardClient()
    transport = AsyncMock()
    transport.__aenter__.return_value = transport
    url = f"{client.base_url}/payment/invoice/owned-uuid"
    transport.delete.return_value = Response(status, content=body, request=Request("DELETE", url))
    monkeypatch.setattr(client, "_get_token", AsyncMock(return_value="synthetic"))
    monkeypatch.setattr("src.payments.service.httpx.AsyncClient", lambda **kwargs: transport)
    assert await client.delete_owned_invoice("owned-uuid") is confirmed
    transport.delete.assert_awaited_once_with(url, headers=client._get_headers("synthetic"))


@pytest.mark.parametrize("state", owner.HELD_STATES)
@pytest.mark.parametrize("product_id", [39, 40])
async def test_quote_excludes_marked_points_hold_at_any_age(
    maker: async_sessionmaker[AsyncSession],
    state: str,
    product_id: int,
) -> None:
    async with maker.begin() as db:
        vest = await db.get(Product, 39)
        assert vest is not None
        vest.price = Decimal("10000")
    oid = await admit(maker, request("L", points=5000))
    async with maker.begin() as db:
        order = await db.get(Order, oid)
        assert order is not None
        order.created_at = datetime.now(UTC) - timedelta(days=2)
        order.size_owner_state = state
        order.payment_status = "failed"
        entry = await db.scalar(select(PointLedger).where(PointLedger.user_id == 1))
        assert entry is not None
        entry.amount = 10000
    data = OrderCreate(
        items=[
            OrderItemCreate(
                product_id=product_id, quantity=1, size="XL" if product_id == 39 else None
            )
        ]
    )
    async with maker() as db:
        summary = await get_loyalty_summary(db, 39001)
        assert summary.available == 5000
        assert summary.reserved_until is None
        quote = await build_quote(db, data, 1)
        assert quote.max_redeemable == 5000
        data.points_to_apply = 10000
        with pytest.raises(InvalidRedeemAmountError) as error:
            await build_quote(db, data, 1)
        assert error.value.max_redeemable == 5000
        stored = await db.get(Order, oid)
        assert stored is not None
        assert stored.size_owner_state == state
        assert stored.items[0].restored_quantity == 0
        assert await get_balance(db, 1) == 10000
    assert await quantities(maker) == {"L": 49, "XL": 50, "2XL": 50}
    data.points_to_apply = 5000
    async with maker() as db:
        legal = await build_quote(db, data, 1)
        data.quote_fingerprint = legal.quote_fingerprint
        admitted = await create_order(db, data, 1)
        assert admitted.points_applied == 5000
        await db.commit()
