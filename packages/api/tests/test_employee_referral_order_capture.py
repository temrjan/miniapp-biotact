"""Mounted checkout, actual K1 receiver and independent durable PostgreSQL readers."""

import copy
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src import database
from src.config import settings
from src.employee_referrals import capture
from src.employee_referrals.models import (
    CampaignReplica,
    ReferralIdentity,
    ReferralOrderItemSnapshot,
    ReferralOrderSnapshot,
)
from src.employee_referrals.schemas import utc_text
from src.loyalty import service as loyalty
from src.loyalty.models import CustomerProfile, PointLedger
from src.main import create_app
from src.notifications.service import telegram_notifier
from src.orders import service as orders
from src.orders.models import Order, OrderItem, OrderStatus
from src.orders.schemas import OrderCreate
from src.products.models import Product
from src.users.models import User
from tests.conftest import _make_init_data
from tests.test_employee_referral_k1_contract import KEY, PATH, WIRE, encoded, signed

TOKEN = "AAAAAAAAAAAAAAAAAAAAAA"
ORDERS = "/api/v1/orders"


@dataclass
class Checkout:
    client: AsyncClient
    app: object
    sessions: async_sessionmaker
    engine: object
    data: dict
    headers: dict
    now: object

    def body(self, **extra):
        return {"items": [{"product_id": 39, "quantity": 1}], "referral_token": TOKEN, **extra}

    async def create(self, body=None, **kwargs):
        return await self.client.post(
            ORDERS, json=self.body() if body is None else body, headers=self.headers, **kwargs
        )

    async def publish(self, data=None):
        raw = encoded(self.data if data is None else data)
        response = await self.client.post(PATH, content=raw, headers=signed(raw))
        assert response.status_code == 200, response.text
        return response

    async def state(self):
        async with self.sessions() as reader:
            counts = [
                await reader.scalar(select(func.count()).select_from(model))
                for model in (Order, ReferralOrderSnapshot, ReferralOrderItemSnapshot)
            ]
            balance = await loyalty.get_balance(reader, 1)
            return (*counts, balance)


@pytest_asyncio.fixture
async def checkout(test_db, monkeypatch):
    monkeypatch.setattr(settings, "employee_referral_capture_enabled", True)
    monkeypatch.setattr(settings, "employee_referral_k1_enabled", True)
    monkeypatch.setattr(settings, "employee_referral_k1_key", SecretStr(KEY.hex()))
    monkeypatch.setattr(telegram_notifier, "notify_payment_success", AsyncMock(return_value=True))
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session_maker", maker)
    async with maker.begin() as seed:
        seed.add(User(id=1, telegram_id=990039, first_name="Synthetic buyer"))
        seed.add_all(
            [
                Product(id=39, name="Synthetic vest", slug="capture-vest", price=Decimal("10000")),
                Product(
                    id=40, name="Ordinary product", slug="capture-other", price=Decimal("20000")
                ),
            ]
        )
        await seed.flush()
        seed.add(CustomerProfile(user_id=1))
        seed.add(PointLedger(user_id=1, amount=100000, reason="adjust"))
        now = await seed.scalar(select(func.clock_timestamp()))
    data = json.loads(WIRE)
    data["issued_at"] = utc_text(now - timedelta(seconds=2))
    data["campaign"]["published_at"] = utc_text(now - timedelta(days=1))
    data["campaign"]["start_at"] = utc_text(now - timedelta(hours=1))
    data["campaign"]["end_at"] = utc_text(now + timedelta(days=1))
    app = create_app()
    app.state.employee_referral_sessions = maker
    headers = {
        "X-Init-Data": _make_init_data(
            settings.telegram_bot_token, {"id": 990039, "first_name": "Synthetic buyer"}
        )
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        h = Checkout(client, app, maker, engine, data, headers, now)
        await h.publish()
        yield h
    await engine.dispose()


async def test_valid_mixed_and_duplicate_lines_are_server_snapshots(checkout):
    h = checkout
    response = await h.create(
        h.body(
            items=[
                {"product_id": 39, "quantity": 2, "product_price": 1},
                {"product_id": 40, "quantity": 1},
                {"product_id": 39, "quantity": 3},
            ],
            employee_code=str(uuid4()),
            campaign_id=999,
            total=1,
        )
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert Decimal(str(body["total"])) == 70000
    assert "referral" not in json.dumps(body)
    async with h.sessions() as reader:
        header = await reader.get(ReferralOrderSnapshot, body["id"])
        lines = list(await reader.scalars(select(ReferralOrderItemSnapshot)))
        order_lines = list(await reader.scalars(select(OrderItem)))
        assert header.employee_display_name == h.data["participants"][0]["employee_display_name"]
        assert header.campaign_name == h.data["campaign"]["name"]
        assert header.campaign_id == header.content_revision == 1
        assert header.product_id == 39
        assert header.currency == "UZS"
        assert header.valid_until - header.issued_at == timedelta(seconds=300)
        assert header.issued_at <= header.captured_at < header.valid_until
        assert sorted(line.quantity for line in lines) == [2, 3]
        assert {line.order_item_id for line in lines} == {
            x.id for x in order_lines if x.product_id == 39
        }
        assert all(x.unit_price == 10000 and x.line_total == x.quantity * 10000 for x in lines)


@pytest.mark.parametrize(
    "token",
    [None, "", " ", "!" * 22, "A" * 21, "A" * 10000, "A" * 21 + "B"],
    ids=["null", "empty", "space", "invalid-chars", "short", "long", "noncanonical"],
)
async def test_absent_or_noncanonical_token_never_reads_k1(checkout, token):
    h = checkout
    statements = []

    def observe(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(h.engine.sync_engine, "before_cursor_execute", observe)
    try:
        assert (await h.create(h.body(referral_token=token))).status_code == 201
    finally:
        event.remove(h.engine.sync_engine, "before_cursor_execute", observe)
    assert not any("employee_referral_" in s for s in statements)
    assert await h.state() == (1, 0, 0, 100000)


@pytest.mark.parametrize("mode", ["off", "omitted", "unknown", "product-mismatch"])
async def test_ordinary_fallback(checkout, monkeypatch, mode):
    h = checkout
    body = h.body()
    if mode == "off":
        monkeypatch.setattr(settings, "employee_referral_capture_enabled", False)
    elif mode == "omitted":
        body.pop("referral_token")
    elif mode == "unknown":
        body["referral_token"] = "AQEBAQEBAQEBAQEBAQEBAQ"
    else:
        body["items"][0]["product_id"] = 40
    statements = []

    def observe(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(h.engine.sync_engine, "before_cursor_execute", observe)
    try:
        assert (await h.create(body)).status_code == 201
    finally:
        event.remove(h.engine.sync_engine, "before_cursor_execute", observe)
    if mode in {"off", "omitted"}:
        assert not any("employee_referral_" in statement for statement in statements)
    assert await h.state() == (1, 0, 0, 100000)


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("start", True),
        ("before-start", False),
        ("before-end", True),
        ("end", False),
        ("before-expiry", True),
        ("expiry", False),
        ("future-issuance", False),
        ("future-publication", False),
        ("omission", False),
        ("ended", False),
    ],
)
async def test_exact_temporal_and_registry_boundaries(checkout, monkeypatch, case, expected):
    h = checkout
    now = h.now
    async with h.sessions.begin() as writer:
        replica = await writer.get(CampaignReplica, 1)
        identity = await writer.get(ReferralIdentity, TOKEN)
        if case in {"start", "before-start"}:
            replica.start_at = now + (
                timedelta(microseconds=1) if case == "before-start" else timedelta()
            )
        elif case in {"end", "before-end"}:
            replica.end_at = now + (
                timedelta(microseconds=1) if case == "before-end" else timedelta()
            )
        elif case in {"expiry", "before-expiry", "future-issuance"}:
            replica.issued_at = (
                now + timedelta(seconds=1)
                if case == "future-issuance"
                else now - timedelta(seconds=60)
            )
            if case == "before-expiry":
                replica.issued_at += timedelta(microseconds=1)
            replica.valid_until = replica.issued_at + timedelta(seconds=60)
        elif case == "future-publication":
            replica.published_at = now + timedelta(microseconds=1)
        elif case == "omission":
            identity.in_current_registry = False
        elif case == "ended":
            replica.state = "ended"
            replica.ended_at = now

    async def exact_clock(_session):
        return now

    monkeypatch.setattr(capture, "capture_clock", exact_clock)
    assert (await h.create()).status_code == 201
    assert (await h.state())[1:3] == (int(expected), int(expected))


@pytest.mark.parametrize("points", [0, 5000, 10000])
async def test_capture_precedes_real_debit_and_paid_transition(checkout, monkeypatch, points):
    h = checkout
    real_reserve = loyalty.reserve_points

    async def observed(db, **kwargs):
        order = await db.get(Order, kwargs["order_id"])
        assert order.payment_status == "unpaid"
        assert order.status == OrderStatus.PENDING
        assert await db.get(ReferralOrderSnapshot, order.id) is not None
        assert await db.scalar(select(func.count()).select_from(ReferralOrderItemSnapshot)) == 1
        assert await loyalty.get_balance(db, 1) == 100000
        await real_reserve(db, **kwargs)

    monkeypatch.setattr(loyalty, "reserve_points", observed)
    response = await h.create(h.body(points_to_apply=points))
    assert response.status_code == 201, response.text
    assert response.json()["payment_status"] == ("paid" if points == 10000 else "unpaid")
    assert await h.state() == (1, 1, 1, 90000 if points == 10000 else 100000)


async def test_quote_and_prices_ignore_token_and_preserve_old_errors(checkout):
    h = checkout
    quotes = []
    for token in (None, TOKEN, "invalid"):
        response = await h.client.post(
            ORDERS + "/quote", json=h.body(referral_token=token), headers=h.headers
        )
        assert response.status_code == 200
        quotes.append(response.json())
    assert quotes[0] == quotes[1] == quotes[2]
    assert await h.state() == (0, 0, 0, 100000)
    assert (await h.create(h.body(quote_fingerprint="incorrect"))).status_code == 409
    assert (await h.create(h.body(points_to_apply=1))).status_code == 422
    assert await h.state() == (0, 0, 0, 100000)


async def test_snapshots_survive_catalogue_and_k1_changes_and_status_update(checkout):
    h = checkout
    body = (await h.create()).json()
    async with h.sessions() as reader:
        before = (
            await reader.execute(
                text("SELECT row_to_json(t)::text FROM employee_referral_orders t")
            )
        ).all()
        before_lines = (
            await reader.execute(
                text("SELECT row_to_json(t)::text FROM employee_referral_order_items t")
            )
        ).all()
    changed = copy.deepcopy(h.data)
    changed["event_id"] = str(uuid4())
    changed["campaign"]["content_revision"] = 2
    changed["participants"][0]["employee_display_name"] = "Changed display"
    await h.publish(changed)
    async with h.sessions.begin() as writer:
        order = await writer.get(Order, body["id"])
        order.payment_status = "paid"
        await writer.execute(delete(Product).where(Product.id == 39))
    async with h.sessions() as reader:
        assert (
            await reader.execute(
                text("SELECT row_to_json(t)::text FROM employee_referral_orders t")
            )
        ).all() == before
        assert (
            await reader.execute(
                text("SELECT row_to_json(t)::text FROM employee_referral_order_items t")
            )
        ).all() == before_lines
        assert await reader.scalar(select(OrderItem.product_id)) is None


async def test_db_uniqueness_and_foreign_item_guard(checkout):
    h = checkout
    first = (await h.create()).json()["id"]
    second = (await h.create(h.body(referral_token=None))).json()["id"]
    async with h.sessions() as writer:
        order = await writer.get(Order, first)
        replica = await writer.get(CampaignReplica, 1)
        identity = await writer.get(ReferralIdentity, TOKEN)
        with pytest.raises(IntegrityError):
            await capture.store_snapshot(writer, order, replica, identity, h.now)
        await writer.rollback()
        first_order = await writer.get(Order, first)
        second_order = await writer.get(Order, second)
        replica = await writer.get(CampaignReplica, 1)
        identity = await writer.get(ReferralIdentity, TOKEN)
        # Corrupt only the in-memory helper input, without flushing it to the DB.
        first_order.items.append(second_order.items[0])
        with writer.no_autoflush, pytest.raises(ValueError, match="ownership"):
            await capture.store_snapshot(writer, first_order, replica, identity, h.now)
        await writer.rollback()
    async with h.sessions() as writer:
        with pytest.raises(IntegrityError):
            await writer.execute(
                text(
                    "INSERT INTO employee_referral_order_items SELECT * FROM employee_referral_order_items"
                )
            )
        await writer.rollback()
    assert await h.state() == (2, 1, 1, 100000)


async def test_capture_echo_guard_precedes_sql(checkout, monkeypatch):
    h = checkout
    monkeypatch.setattr(settings, "database_echo", True)
    assert (await h.create()).status_code == 503
    async with h.sessions() as session:
        statements = []

        def observe(_conn, _cursor, statement, _params, _context, _many):
            statements.append(statement)

        event.listen(h.engine.sync_engine, "before_cursor_execute", observe)
        try:
            with pytest.raises(capture.CaptureUnavailableError):
                await orders.create_order(session, OrderCreate.model_validate(h.body()), user_id=1)
        finally:
            event.remove(h.engine.sync_engine, "before_cursor_execute", observe)
        assert not statements
    assert (await h.create(h.body(referral_token=None))).status_code == 201
    assert await h.state() == (1, 0, 0, 100000)


async def test_sale_snapshot_and_quote_do_not_use_attribution(checkout):
    h = checkout
    async with h.sessions.begin() as writer:
        product = await writer.get(Product, 39)
        product.is_sale = True
        product.sale_price = Decimal("8000")
    body = h.body(items=[{"product_id": 39, "quantity": 2}])
    statements = []

    def observe(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement)

    event.listen(h.engine.sync_engine, "before_cursor_execute", observe)
    try:
        with_token = await h.client.post(ORDERS + "/quote", json=body, headers=h.headers)
        without_token = await h.client.post(
            ORDERS + "/quote", json={"items": body["items"]}, headers=h.headers
        )
    finally:
        event.remove(h.engine.sync_engine, "before_cursor_execute", observe)
    assert with_token.status_code == without_token.status_code == 200
    assert with_token.json() == without_token.json()
    assert not any("employee_referral_" in statement for statement in statements)
    assert (await h.state())[:3] == (0, 0, 0)
    refused = await h.create(body)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "quote_required"
    assert (await h.state())[:3] == (0, 0, 0)
    body["quote_fingerprint"] = with_token.json()["quote_fingerprint"]
    response = await h.create(body)
    assert response.status_code == 201, response.text
    assert Decimal(str(response.json()["total"])) == 16000
    async with h.sessions() as reader:
        line = await reader.scalar(select(ReferralOrderItemSnapshot))
        assert line.quantity == 2
        assert line.unit_price == 8000
        assert line.line_total == 16000
        assert line.is_sale_at_order
        assert line.regular_price_at_order == 10000


async def test_snapshot_rejects_unpersisted_order_and_items(checkout):
    h = checkout
    async with h.sessions() as writer:
        replica = await writer.get(CampaignReplica, 1)
        identity = await writer.get(ReferralIdentity, TOKEN)
        with pytest.raises(ValueError, match="ownership"):
            await capture.store_snapshot(writer, Order(), replica, identity, h.now)
        order = Order(id=999)
        order.items = [OrderItem(order_id=999, product_id=39)]
        with pytest.raises(ValueError, match="ownership"):
            await capture.store_snapshot(writer, order, replica, identity, h.now)
    assert await h.state() == (0, 0, 0, 100000)
