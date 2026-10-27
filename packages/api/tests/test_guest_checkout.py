"""G1 website guest checkout (spec S2) on an actually migrated, disposable PostgreSQL 16.

Each test owns a scratch database built by ``alembic upgrade head``. Requests run through a
``get_db`` override with the production commit-after-response / rollback-on-raise semantics.
The Multicard client runs unchanged; only its HTTP transport is substituted. Telegram is replaced
at ``send_message``. Set S1_TEST_DATABASE_URL to the local disposable server's postgres database.
"""

import asyncio
import hashlib
import json
import time
import types
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib import import_module
from typing import Any

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src import config as config_module
from src.auth.privacy import filter_auth_telemetry
from src.buyer_auth.models import BuyerAuthLimit
from src.buyer_auth.security import BuyerAuthError, keyed
from src.buyer_auth.service import BuyerAuthService
from src.config import Settings
from src.database import get_db
from src.guest import service as guest_service
from src.notifications.service import telegram_notifier
from src.orders.models import Order, OrderStatus
from src.payments import service as payment_service
from src.payments.service import multicard_client
from tests import test_category_schema_migration as migration
from tests.conftest import _make_init_data

scratch = migration.scratch
PATH = "/api/v1/guest"
ORIGIN = "https://shop.g1.test"
API_ORIGIN = "https://api.g1.test"
PROVIDER = "https://mesh.g1.test"
SECRET = "g1-synthetic-multicard-secret"
BOT_TOKEN = "g1-synthetic-mini-app-bot"
COOKIE = "__Host-biotact_guest"
STATUS_KEYS = {"order_number", "state", "lines", "total", "currency", "csrf_token"}


def configuration(**changes: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "database_url": "postgresql+asyncpg://synthetic:synthetic@127.0.0.1/synthetic",
        "secret_key": SecretStr("g1-synthetic-admin-only"),
        "guest_checkout_enabled": True,
        "buyer_protection_key": SecretStr("g1-synthetic-protection-key-at-least-32-bytes"),
        "buyer_public_origin": ORIGIN,
        "payment_callback_base_url": API_ORIGIN,
        "guest_terms_version": "terms-g1-synthetic",
        "guest_privacy_version": "policy-g1-synthetic",
        "cors_origins": [ORIGIN],
        "multicard_store_id": "6",
    }
    values.update(changes)
    return Settings(**values)


SEED = """
INSERT INTO categories (id,name,slug,sort_order,is_active,is_purchasable)
VALUES (801,'G1 category','g1-category',1,true,true);
INSERT INTO products (id,name,slug,price,is_sale,sale_price,currency,stock_quantity,is_in_stock,
                      category_id,sort_order,is_active,is_featured,size_cutover_blocked,
                      sizes_initialized_at)
VALUES (501,'G1 ordinary','g1-ordinary',60000.00,false,NULL,'UZS',5,true,801,1,true,false,false,NULL),
       (502,'G1 sale','g1-sale',100000.00,true,80000.00,'UZS',5,true,801,2,true,false,false,NULL),
       (503,'G1 delisted','g1-delisted',10000.00,false,NULL,'UZS',5,true,801,3,false,false,false,NULL),
       (504,'G1 out','g1-out',10000.00,false,NULL,'UZS',0,false,801,4,true,false,false,NULL),
       (505,'G1 vest','g1-vest',30000.00,false,NULL,'UZS',0,true,801,5,true,false,false,now());
INSERT INTO product_sizes (product_id,size,available_quantity,revision)
VALUES (505,'L',50,0),(505,'XL',50,0),(505,'2XL',0,0);
"""


@dataclass
class Provider:
    """Multicard at its HTTP boundary; the real ``MultiCardClient`` code builds every request."""

    mode: str = "ok"
    hold: int = 0
    auth: int = 0
    creates: list[dict[str, Any]] = field(default_factory=list)
    deletes: list[str] = field(default_factory=list)
    reads: list[str] = field(default_factory=list)
    arrived: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/auth":
            self.auth += 1
            return httpx.Response(200, json={"token": "synthetic-token"})
        if request.method == "POST" and path == "/payment/invoice":
            payload = json.loads(request.content)
            self.creates.append(payload)
            number = len(self.creates)
            if self.hold and number <= self.hold:
                if number == self.hold:
                    self.arrived.set()
                await asyncio.wait_for(self.arrived.wait(), 10)
            if self.mode == "500":
                return httpx.Response(500, json={"success": False})
            if self.mode == "timeout":
                raise httpx.ReadTimeout("synthetic timeout", request=request)
            if self.mode == "refused":
                return httpx.Response(200, json={"success": False, "error": {"details": "no"}})
            uuid = f"g1-invoice-{number}"
            data: dict[str, Any] = {"uuid": "" if self.mode == "empty_uuid" else uuid}
            if self.mode != "no_url":
                data["checkout_url"] = f"https://pay.g1.test/{uuid}"
            return httpx.Response(200, json={"success": True, "data": data})
        if request.method == "DELETE":
            self.deletes.append(path)
            return httpx.Response(200, json={"success": True})
        self.reads.append(path)
        return httpx.Response(200, json={"success": False})


@dataclass
class Guest:
    app: Any
    sessions: async_sessionmaker[AsyncSession]
    client: AsyncClient
    provider: Provider
    messages: list[str]
    seen_bound: list[bool]
    config: Settings
    db: migration.ScratchDatabase
    fail_commit: list[bool]

    def browser(self, **kwargs: Any) -> AsyncClient:
        transport = ASGITransport(app=self.app, **kwargs)
        return AsyncClient(transport=transport, base_url=ORIGIN, headers={"Origin": ORIGIN})

    def sql(self, statement: str, args: tuple | None = None) -> list:
        return self.db.execute(statement, args)


@pytest_asyncio.fixture
async def guest(scratch, monkeypatch) -> AsyncGenerator[Guest, None]:
    await asyncio.to_thread(scratch.migrate, target="head")
    scratch.execute(SEED)
    engine = create_async_engine(scratch.url, pool_size=10)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    config = configuration(database_url=scratch.url)
    main = import_module("src.main")
    monkeypatch.setattr(main, "settings", config)
    monkeypatch.setattr(main, "async_session_maker", sessions)
    app = main.create_app()
    fail_commit: list[bool] = []

    async def request_db() -> AsyncGenerator[AsyncSession, None]:
        async with sessions() as db:
            if fail_commit:
                fail_commit.clear()

                async def failing_commit() -> None:
                    raise OperationalError("COMMIT", {}, RuntimeError("synthetic commit failure"))

                db.commit = failing_commit  # type: ignore[method-assign]
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    app.dependency_overrides[get_db] = request_db
    provider = Provider()
    real_client = httpx.AsyncClient

    def provider_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        return real_client(*args, transport=httpx.MockTransport(provider), **kwargs)

    monkeypatch.setattr(
        payment_service,
        "httpx",
        types.SimpleNamespace(
            AsyncClient=provider_client,
            HTTPStatusError=httpx.HTTPStatusError,
            HTTPError=httpx.HTTPError,
        ),
    )
    monkeypatch.setattr(multicard_client, "base_url", PROVIDER)
    monkeypatch.setattr(multicard_client, "secret", SECRET)
    monkeypatch.setattr(multicard_client, "store_id", "6")
    monkeypatch.setattr(multicard_client, "_token", None)
    monkeypatch.setattr(multicard_client, "_token_expiry", None)
    monkeypatch.setattr(config_module.settings, "multicard_store_id", "6")
    monkeypatch.setattr(config_module.settings, "telegram_bot_token", BOT_TOKEN)
    messages: list[str] = []
    seen_bound: list[bool] = []

    async def send(text_value: str, parse_mode: str = "HTML") -> bool:
        if "НОВЫЙ ЗАКАЗ" in text_value:
            # Another connection must already see the committed bind (PY-02 f).
            number = text_value.split("Заказ:</b> ", 1)[1].split("\n", 1)[0]
            rows = await asyncio.to_thread(
                scratch.execute,
                "SELECT checkout_url IS NOT NULL FROM orders WHERE order_number=%s",
                (number,),
            )
            seen_bound.append(bool(rows and rows[0][0]))
        messages.append(text_value)
        return True

    monkeypatch.setattr(telegram_notifier, "send_message", send)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=ORIGIN, headers={"Origin": ORIGIN}
    ) as client:
        yield Guest(
            app, sessions, client, provider, messages, seen_bound, config, scratch, fail_commit
        )
    await engine.dispose()


# --------------------------------------------------------------------------- helpers


ORDINARY = [{"product_id": 501, "quantity": 2, "size": None}]


def body(items: list[dict[str, Any]], fingerprint: str, key: str = "a" * 32, **changes: Any):
    value = {
        "items": items,
        "quote_fingerprint": fingerprint,
        "name": "  G1 Buyer  ",
        "phone": " +998 (90) 123-45-67 ",
        "address": "  Tashkent, synthetic street 1  ",
        "accept_terms": True,
        "idempotency_key": key,
    }
    value.update(changes)
    return value


async def quote(client: AsyncClient, items: list[dict[str, Any]]) -> dict[str, Any]:
    response = await client.post(PATH + "/quote", json={"items": items})
    assert response.status_code == 200, response.text
    return response.json()


async def place(
    client: AsyncClient, items: list[dict[str, Any]] = ORDINARY, key: str = "a" * 32, **changes
) -> httpx.Response:
    fingerprint = (await quote(client, items))["quote_fingerprint"]
    return await client.post(PATH + "/orders", json=body(items, fingerprint, key, **changes))


async def pay(client: AsyncClient, csrf: str, lang: str = "uz", **headers) -> httpx.Response:
    return await client.post(
        PATH + "/order/payment", json={"lang": lang}, headers={"X-CSRF-Token": csrf, **headers}
    )


def count(guest: Guest, table: str) -> int:
    return guest.sql(f"SELECT count(*) FROM {table}")[0][0]  # noqa: S608 - fixed test names


def sizes(guest: Guest) -> list[tuple[str, int]]:
    return guest.sql("SELECT size, available_quantity FROM product_sizes ORDER BY size")


def md5(invoice_id: str, amount: int) -> str:
    return hashlib.md5(f"6{invoice_id}{amount}{SECRET}".encode()).hexdigest()


def callback(invoice_id: str, uuid: str, amount: int) -> dict[str, Any]:
    return {
        "store_id": 6,
        "amount": amount,
        "invoice_id": invoice_id,
        "uuid": uuid,
        "ps": "uzcard",
        "sign": md5(invoice_id, amount),
    }


# --------------------------------------------------------------------------- CK-03


@pytest.mark.parametrize(
    "change",
    [
        {"buyer_protection_key": SecretStr("short")},
        {"buyer_protection_key": SecretStr("g1-synthetic-admin-only-and-long-enough")},
        {"buyer_public_origin": "http://shop.g1.test"},
        {"buyer_public_origin": "https://shop.g1.test/path"},
        {"payment_callback_base_url": ""},
        {"payment_callback_base_url": "https://api.g1.test/"},
        {"guest_terms_version": ""},
        {"guest_privacy_version": "x" * 65},
        {"guest_terms_version": "draft\n2026"},
        {"buyer_trusted_proxy_peers": ["not-a-network"]},
        {"database_echo": True},
    ],
)
def test_enabled_flag_refuses_unsafe_configuration(change):
    if change.get("buyer_protection_key") == SecretStr("g1-synthetic-admin-only-and-long-enough"):
        change["secret_key"] = SecretStr("g1-synthetic-admin-only-and-long-enough")
    with pytest.raises(ValueError, match=r"Invalid guest checkout configuration|does not appear"):
        configuration(**change)


def test_disabled_flag_needs_no_guest_configuration():
    config = Settings(
        _env_file=None, database_url="postgresql+asyncpg://x:y@127.0.0.1/z", secret_key="s"
    )
    assert config.guest_checkout_enabled is False
    assert configuration().guest_checkout_enabled is True
    assert configuration(guest_terms_version="draft-2026-08-20 rev 1").guest_terms_version


async def test_disabled_flag_answers_404_before_any_effect(scratch, monkeypatch):
    await asyncio.to_thread(scratch.migrate, target="head")
    scratch.execute(SEED)
    main = import_module("src.main")
    monkeypatch.setattr(
        main, "settings", configuration(database_url=scratch.url, guest_checkout_enabled=False)
    )
    app = main.create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN) as client:
        for method, path, payload in (
            ("POST", "/quote", {"items": "invalid"}),
            ("POST", "/orders", {"bad": True}),
            ("GET", "/order", None),
            ("POST", "/order/payment", {"lang": "xx"}),
            ("GET", "/anything", None),
        ):
            response = await client.request(
                method,
                PATH + path,
                json=payload,
                headers={"Origin": "https://evil.test", "Content-Type": "text/plain"},
                cookies={COOKIE: "f" * 64},
            )
            assert response.status_code == 404
            assert response.json() == {"error": "guest_checkout_disabled"}
            assert response.headers["cache-control"] == "no-store"
    assert scratch.execute("SELECT count(*) FROM orders") == [(0,)]
    assert scratch.execute("SELECT count(*) FROM buyer_auth_limits") == [(0,)]


# --------------------------------------------------------------------------- CK-04


@pytest.mark.parametrize(
    ("headers", "status", "code"),
    [
        ({"Origin": None}, 403, "request_forbidden"),
        ({"Origin": "null"}, 403, "request_forbidden"),
        ({"Origin": "https://evil.test"}, 403, "request_forbidden"),
        ({"Origin": "https://sub.shop.g1.test"}, 403, "request_forbidden"),
        ({"Origin": "https://shop.g1.test.evil.test"}, 403, "request_forbidden"),
        ({"Host": "evil.test"}, 403, "request_forbidden"),
        ({"Content-Type": "text/plain"}, 415, "json_required"),
    ],
)
async def test_boundary_refusals_have_no_effect(guest, headers, status, code):
    send = {"Origin": ORIGIN, "Content-Type": "application/json"}
    for name, value in headers.items():
        if value is None:
            send.pop(name)
        else:
            send[name] = value
    content = json.dumps(body(ORDINARY, "0" * 64)).encode()
    async with guest.browser() as client:
        client.headers.pop("Origin")
        for path in ("/quote", "/orders", "/order/payment"):
            response = await client.post(PATH + path, content=content, headers=send)
            assert (response.status_code, response.json()) == (status, {"error": code})
            assert response.headers["cache-control"] == "no-store"
            assert not any(h.startswith("access-control-") for h in response.headers)
    assert count(guest, "orders") == 0
    assert count(guest, "buyer_auth_limits") == 0


async def test_body_cap_streamed_cors_and_no_echo(guest):
    big = json.dumps({"items": ORDINARY, "pad": "x" * 4096}).encode()
    response = await guest.client.post(
        PATH + "/orders", content=big, headers={"Content-Type": "application/json"}
    )
    assert (response.status_code, response.json()) == (413, {"error": "request_too_large"})

    async def chunks():
        yield b'{"items": ['
        yield b" " * 4096
        yield b"]}"

    response = await guest.client.post(
        PATH + "/quote", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert (response.status_code, response.json()) == (413, {"error": "request_too_large"})
    canary = "ECHO_CANARY_G1"
    response = await guest.client.post(
        PATH + "/orders", json=body(ORDINARY, "0" * 64, name=canary, extra=canary)
    )
    assert (response.status_code, response.json()) == (422, {"error": "invalid_request"})
    assert canary not in response.text
    # Successful guest responses to a CORS-listed Origin carry no Access-Control-* header.
    quoted = await guest.client.post(PATH + "/quote", json={"items": ORDINARY})
    created = await place(guest.client)
    status = await guest.client.get(PATH + "/order")
    for response in (quoted, created, status):
        assert response.status_code in (200, 201)
        assert response.headers["cache-control"] == "no-store"
        assert not any(h.startswith("access-control-") for h in response.headers)
        assert "etag" not in response.headers
        assert "last-modified" not in response.headers
    control = await guest.client.get("/api/v1/products")
    assert control.headers["access-control-allow-origin"] == ORIGIN
    assert count(guest, "orders") == 1


# --------------------------------------------------------------------------- CK-07, CK-08


def _phone(digits: int, extra: str = "") -> str:
    return "+" + "9" * digits + extra


INVALID = [
    ("extra", {"points_to_apply": 0}, "invalid_request"),
    ("referral", {"referral_token": "x"}, "invalid_request"),
    ("note", {"customer_note": "x"}, "invalid_request"),
    ("name_blank", {"name": "   "}, "invalid_request"),
    ("name_101", {"name": "n" * 101}, "invalid_request"),
    ("name_control", {"name": "G1\x07Buyer"}, "invalid_request"),
    ("name_type", {"name": 5}, "invalid_request"),
    ("phone_syntax", {"phone": "+998#901234567"}, "invalid_request"),
    ("phone_8", {"phone": _phone(8)}, "invalid_request"),
    ("phone_16", {"phone": _phone(16)}, "invalid_request"),
    ("phone_21_chars", {"phone": "1 2 3 4 5 6 7 8 9 0 1"}, "invalid_request"),
    ("phone_inner_plus", {"phone": "998+901234567"}, "invalid_request"),
    ("address_blank", {"address": " \n "}, "invalid_request"),
    ("address_301", {"address": "a" * 301}, "invalid_request"),
    ("address_control", {"address": "street\x00 1"}, "invalid_request"),
    ("key_upper", {"idempotency_key": "A" * 32}, "invalid_request"),
    ("key_short", {"idempotency_key": "a" * 31}, "invalid_request"),
    ("fingerprint_bad", {"quote_fingerprint": "g" * 64}, "invalid_request"),
    ("terms_false", {"accept_terms": False}, "terms_not_accepted"),
    ("terms_string", {"accept_terms": "true"}, "terms_not_accepted"),
    ("terms_one", {"accept_terms": 1}, "terms_not_accepted"),
    ("terms_missing", {"accept_terms": None}, "terms_not_accepted"),
    ("items_empty", {"items": []}, "invalid_request"),
    (
        "qty_string",
        {"items": [{"product_id": 501, "quantity": "1", "size": None}]},
        "invalid_request",
    ),
    ("qty_100", {"items": [{"product_id": 501, "quantity": 100, "size": None}]}, "invalid_request"),
    (
        "id_range",
        {"items": [{"product_id": 2147483648, "quantity": 1, "size": None}]},
        "invalid_request",
    ),
    (
        "size_long",
        {"items": [{"product_id": 505, "quantity": 1, "size": "XXXL"}]},
        "invalid_request",
    ),
    (
        "line_extra",
        {"items": [{"product_id": 501, "quantity": 1, "size": None, "price": 1}]},
        "invalid_request",
    ),
]


@pytest.mark.parametrize(("case", "change", "code"), INVALID, ids=[case for case, _, _ in INVALID])
async def test_invalid_create_bodies_refused_without_effect(guest, case, change, code):
    payload = body(ORDINARY, "0" * 64)
    payload.update(change)  # ``change`` may replace ``items`` itself.
    if case == "terms_missing":
        payload.pop("accept_terms")
    response = await guest.client.post(PATH + "/orders", json=payload)
    assert (response.status_code, response.json()) == (422, {"error": code})
    assert count(guest, "orders") == 0
    assert count(guest, "buyer_auth_limits") == 0
    assert sizes(guest) == [("2XL", 0), ("L", 50), ("XL", 50)]


async def test_quote_refuses_out_of_range_product_id_and_bad_shapes(guest):
    for payload in (
        {"items": [{"product_id": 2147483648, "quantity": 1, "size": None}]},
        {"items": [{"product_id": 501, "quantity": 1}]},
        {"items": []},
        [],
    ):
        response = await guest.client.post(PATH + "/quote", json=payload)
        assert (response.status_code, response.json()) == (422, {"error": "invalid_request"})


async def test_accepted_boundaries_and_size_limits(guest):
    keys = iter(f"{index:032x}" for index in range(1, 50))
    for change in (
        {"name": "n" * 100},
        {"address": "a" * 300},
        {"phone": "123456789"},
        {"phone": _phone(15)},
    ):
        response = await place(guest.client, key=next(keys), **change)
        assert response.status_code == 201, response.text
    twenty = [{"product_id": 501, "quantity": 1, "size": None}] * 20
    assert (await place(guest.client, twenty, key=next(keys))).status_code == 201
    ten = [
        {"product_id": 505, "quantity": 5, "size": "L"},
        {"product_id": 505, "quantity": 5, "size": "XL"},
    ]
    assert (await place(guest.client, ten, key=next(keys))).status_code == 201
    assert sizes(guest) == [("2XL", 0), ("L", 45), ("XL", 45)]
    orders = count(guest, "orders")
    for items in (
        [*twenty, {"product_id": 501, "quantity": 1, "size": None}],
        [*ten, {"product_id": 505, "quantity": 1, "size": "L"}],
    ):
        for path in ("/quote", "/orders"):
            response = await guest.client.post(
                PATH + path,
                json=body(items, "0" * 64, key=next(keys))
                if path == "/orders"
                else {"items": items},
            )
            assert (response.status_code, response.json()) == (422, {"error": "order_too_large"})
    assert count(guest, "orders") == orders
    assert sizes(guest) == [("2XL", 0), ("L", 45), ("XL", 45)]


async def test_quote_values_and_no_club_effects(guest):
    items = [
        {"product_id": 501, "quantity": 2, "size": None},
        {"product_id": 502, "quantity": 1, "size": None},
        {"product_id": 505, "quantity": 1, "size": "L"},
    ]
    result = await quote(guest.client, items)
    assert [
        (line["product_id"], line["quantity"], line["unit_price"], line["line_total"], line["size"])
        for line in result["lines"]
    ] == [
        (501, 2, "60000.00", "120000.00", None),
        (502, 1, "80000.00", "80000.00", None),
        (505, 1, "30000.00", "30000.00", "L"),
    ]
    assert (result["subtotal"], result["delivery_fee"], result["total"]) == (
        "230000.00",
        "0.00",
        "230000.00",
    )
    assert (result["max_redeemable"], result["points_to_apply"]) == (0, 0)
    assert sizes(guest) == [("2XL", 0), ("L", 50), ("XL", 50)]
    assert count(guest, "orders") == 0
    response = await guest.client.post(
        PATH + "/orders", json=body(items, result["quote_fingerprint"])
    )
    assert response.status_code == 201
    row = guest.sql(
        "SELECT subtotal, delivery_fee, total, points_applied, status::text, payment_status, "
        "size_stock_protocol, size_owner_state, accepted_settlement, user_id FROM orders"
    )
    assert row == [
        (
            Decimal("230000.00"),
            Decimal("0.00"),
            Decimal("230000.00"),
            0,
            "PENDING",
            "unpaid",
            "size_stock_v1",
            "not_started",
            False,
            None,
        )
    ]
    assert sizes(guest) == [("2XL", 0), ("L", 49), ("XL", 50)]
    for table in (
        "point_ledger",
        "customer_profile",
        "employee_referral_orders",
        "employee_referral_order_items",
        "users",
    ):
        assert count(guest, table) == 0, table


async def test_price_change_and_unavailable_items_leave_no_order(guest):
    items = [
        {"product_id": 501, "quantity": 1, "size": None},
        {"product_id": 505, "quantity": 2, "size": "L"},
    ]
    stale = await quote(guest.client, items)
    guest.sql("UPDATE products SET price=65000.00 WHERE id=501")
    response = await guest.client.post(
        PATH + "/orders", json=body(items, stale["quote_fingerprint"])
    )
    assert response.status_code == 409
    assert response.json()["error"] == "quote_changed"
    assert set(response.json()) == {"error", "quote"}
    assert response.json()["quote"]["total"] == "125000.00"
    assert response.json()["quote"]["quote_fingerprint"] != stale["quote_fingerprint"]
    assert count(guest, "orders") == 0
    assert sizes(guest) == [("2XL", 0), ("L", 50), ("XL", 50)]
    for lines, ids in (
        (
            [
                {"product_id": 503, "quantity": 1, "size": None},
                {"product_id": 9999, "quantity": 1, "size": None},
            ],
            [503, 9999],
        ),
        ([{"product_id": 504, "quantity": 1, "size": None}], [504]),
        ([{"product_id": 505, "quantity": 1, "size": "2XL"}], [505]),
        ([{"product_id": 505, "quantity": 1, "size": None}], [505]),
        ([{"product_id": 505, "quantity": 1, "size": "S"}], [505]),
        ([{"product_id": 501, "quantity": 1, "size": "L"}], [501]),
    ):
        for path in ("/quote", "/orders"):
            payload = {"items": lines} if path == "/quote" else body(lines, "0" * 64)
            response = await guest.client.post(PATH + path, json=payload)
            assert response.status_code == 409, (lines, response.text)
            assert response.json() == {"error": "items_unavailable", "product_ids": ids}
    guest.sql("UPDATE products SET size_cutover_blocked=true WHERE id=505")
    response = await guest.client.post(
        PATH + "/quote", json={"items": [{"product_id": 505, "quantity": 1, "size": "L"}]}
    )
    assert response.json() == {"error": "items_unavailable", "product_ids": [505]}
    assert count(guest, "orders") == 0
    assert sizes(guest) == [("2XL", 0), ("L", 50), ("XL", 50)]


# --------------------------------------------------------------------------- CK-09, CK-11


async def test_stored_fields_cookie_and_status_projection(guest):
    before = datetime.now(UTC)
    response = await place(guest.client)
    after = datetime.now(UTC)
    assert response.status_code == 201
    cookie = response.headers["set-cookie"]
    attributes = [part.strip() for part in cookie.split(";")]
    secret = attributes[0].split("=", 1)[1]
    assert attributes[0].startswith(COOKIE + "=")
    assert len(secret) == 64
    assert int(secret, 16) >= 0
    assert secret == secret.lower()
    lowered = {part.lower() for part in attributes[1:]}
    assert lowered == {"httponly", "max-age=2592000", "path=/", "samesite=lax", "secure"}
    created = response.json()
    assert set(created) == STATUS_KEYS
    assert created["state"] == "awaiting_payment"
    assert created["lines"] == [
        {
            "product_id": 501,
            "product_name": "G1 ordinary",
            "size": None,
            "quantity": 2,
            "unit_price": "60000.00",
            "line_total": "120000.00",
        }
    ]
    assert (created["total"], created["currency"]) == ("120000.00", "UZS")
    assert created["csrf_token"] == keyed(guest.config, "guest-csrf", secret)
    row = guest.sql(
        "SELECT delivery_name, delivery_phone, delivery_address, customer_note, "
        "legal_terms_version, legal_privacy_version, legal_accepted_at, guest_access_digest, "
        "guest_idempotency_digest, row_to_json(orders)::text FROM orders"
    )[0]
    assert row[:6] == (
        "G1 Buyer",
        "+998901234567",
        "Tashkent, synthetic street 1",
        None,
        "terms-g1-synthetic",
        "policy-g1-synthetic",
    )
    assert before - timedelta(seconds=1) <= row[6] <= after + timedelta(seconds=1)
    assert row[7] == keyed(guest.config, "guest-access", secret)
    assert row[8] == keyed(guest.config, "guest-idempotency", "a" * 32)
    assert len(row[7]) == len(row[8]) == 64
    assert row[7] != secret
    assert secret not in row[9]
    assert "a" * 32 not in row[9]
    status = await guest.client.get(PATH + "/order")
    assert status.status_code == 200
    assert status.json() == created
    assert "set-cookie" not in status.headers


async def test_national_phone_is_stored_without_country_code(guest):
    response = await place(guest.client, phone="90 123 45 67")
    assert response.status_code == 201
    assert guest.sql("SELECT delivery_phone FROM orders") == [("901234567",)]


async def test_cookie_is_the_only_access_path(guest):
    async with guest.browser() as a, guest.browser() as b:
        first = await place(a, key="1" * 32)
        second = await place(b, [{"product_id": 502, "quantity": 1, "size": None}], key="2" * 32)
        assert first.status_code == second.status_code == 201
        other_id = guest.sql(
            "SELECT id FROM orders WHERE order_number=%s", (second.json()["order_number"],)
        )[0][0]
        response = await a.get(
            PATH + "/order",
            params={"order_number": second.json()["order_number"], "id": other_id},
        )
        assert response.json()["order_number"] == first.json()["order_number"]
        assert (await b.get(PATH + "/order")).json()["order_number"] == second.json()[
            "order_number"
        ]
    for value in (None, "F" * 64, "f" * 63, "0" * 64, "../" + "f" * 61):
        async with guest.browser() as client:
            if value is not None:
                client.cookies.set(COOKIE, value)
            response = await client.get(PATH + "/order")
            assert (response.status_code, response.json()) == (404, {"error": "order_not_found"})
    guest.sql("UPDATE orders SET created_at = now() - interval '30 days'")
    async with guest.browser() as client:
        client.cookies.set(COOKIE, first.headers["set-cookie"].split(";")[0].split("=", 1)[1])
        response = await client.get(PATH + "/order")
        assert (response.status_code, response.json()) == (404, {"error": "order_not_found"})
    guest.sql("UPDATE orders SET created_at = now() - interval '29 days 23 hours'")
    async with guest.browser() as client:
        client.cookies.set(COOKIE, first.headers["set-cookie"].split(";")[0].split("=", 1)[1])
        assert (await client.get(PATH + "/order")).status_code == 200
    assert guest.provider.creates == []


async def test_commit_failure_sets_no_cookie_and_leaves_no_row(guest):
    fingerprint = (await quote(guest.client, ORDINARY))["quote_fingerprint"]
    guest.fail_commit.append(True)
    response = await guest.client.post(PATH + "/orders", json=body(ORDINARY, fingerprint))
    assert (response.status_code, response.json()) == (503, {"error": "service_unavailable"})
    assert "set-cookie" not in response.headers
    assert count(guest, "orders") == 0
    assert sizes(guest) == [("2XL", 0), ("L", 50), ("XL", 50)]


# --------------------------------------------------------------------------- CK-10


async def test_same_key_replays_without_cookie_and_different_keys_create(guest):
    first = await place(guest.client)
    assert first.status_code == 201
    async with guest.browser() as client:
        replay = await place(client, name="Other", phone="+998911111111", address="Other")
        assert replay.status_code == 200
        assert replay.json() == {
            "order_number": first.json()["order_number"],
            "state": "awaiting_payment",
        }
        assert "set-cookie" not in replay.headers
        assert COOKIE not in client.cookies
    assert count(guest, "orders") == 1
    assert (await place(guest.client, key="b" * 32)).status_code == 201
    assert count(guest, "orders") == 2


async def test_concurrent_same_key_sized_order_allocates_once(guest, monkeypatch):
    items = [{"product_id": 505, "quantity": 3, "size": "L"}]
    fingerprint = (await quote(guest.client, items))["quote_fingerprint"]
    barrier = asyncio.Barrier(2)
    calls = 0
    original = guest_service.find_by_idempotency

    async def overlapping(db, digest):
        nonlocal calls
        found = await original(db, digest)
        calls += 1
        if calls <= 2:
            # Both requests have looked and found nothing before either inserts.
            assert found is None
            await asyncio.wait_for(barrier.wait(), 10)
        return found

    monkeypatch.setattr(guest_service, "find_by_idempotency", overlapping)
    async with guest.browser() as one, guest.browser() as two:
        responses = await asyncio.gather(
            one.post(PATH + "/orders", json=body(items, fingerprint)),
            two.post(PATH + "/orders", json=body(items, fingerprint)),
        )
    assert sorted(response.status_code for response in responses) == [200, 201]
    winner = next(r for r in responses if r.status_code == 201)
    loser = next(r for r in responses if r.status_code == 200)
    assert loser.json() == {
        "order_number": winner.json()["order_number"],
        "state": "awaiting_payment",
    }
    assert "set-cookie" not in loser.headers
    assert count(guest, "orders") == 1
    assert sizes(guest) == [("2XL", 0), ("L", 47), ("XL", 50)]


# --------------------------------------------------------------------------- CK-06


async def test_durable_peer_limits(guest, monkeypatch):
    keys = iter(f"{index:032x}" for index in range(1, 40))
    for _ in range(10):
        assert (await place(guest.client, key=next(keys))).status_code == 201
    response = await place(guest.client, key=next(keys))
    assert response.status_code == 429
    assert response.json() == {"error": "try_later"}
    assert int(response.headers["retry-after"]) >= 3599
    assert count(guest, "orders") == 10
    # An untrusted peer cannot choose its identity through X-Real-IP.
    fingerprint = (await quote(guest.client, ORDINARY))["quote_fingerprint"]
    response = await guest.client.post(
        PATH + "/orders",
        json=body(ORDINARY, fingerprint, next(keys)),
        headers={"X-Real-IP": "192.0.2.77"},
    )
    assert response.status_code == 429
    # The durable budget is shared by a new service instance (another process).
    second = BuyerAuthService(guest.config, guest.sessions)
    with pytest.raises(BuyerAuthError, match="try_later"):
        await second.account("guest-order-peer", "127.0.0.1", 10, 3600)
    async with guest.browser(client=("192.0.2.10", 4000)) as other:
        assert (await place(other, key=next(keys))).status_code == 201
    # Through a configured proxy peer, X-Real-IP names the client.
    monkeypatch.setattr(guest.config, "buyer_trusted_proxy_peers", ["127.0.0.1/32"])
    response = await guest.client.post(
        PATH + "/orders",
        json=body(ORDINARY, fingerprint, next(keys)),
        headers={"X-Real-IP": "192.0.2.77"},
    )
    assert response.status_code == 201
    assert count(guest, "orders") == 12
    async with guest.sessions() as db:
        row = await db.get(
            BuyerAuthLimit, keyed(guest.config, "rate-guest-order-peer", "127.0.0.1")
        )
        assert len(row.events) == 10
        assert "127.0.0.1" not in row.key


# --------------------------------------------------------------------------- CK-12


async def test_state_projection_for_each_shape(guest):
    created = await place(guest.client)
    number = created.json()["order_number"]

    async def current() -> str:
        return (await guest.client.get(PATH + "/order")).json()["state"]

    assert await current() == "awaiting_payment"
    for statement, expected in (
        ("payment_uuid='u', payment_status='pending'", "awaiting_payment"),
        ("payment_status='paid', status='CONFIRMED'", "paid"),
        ("payment_status='failed', status='CANCELLED'", "cancelled"),
        ("payment_status='pending', status='CANCELLED'", "cancelled"),
        ("payment_status='refunded', status='CANCELLED'", "cancelled"),
        ("payment_status='refunded', status='CONFIRMED'", "cancelled"),
        ("payment_status='failed', status='PENDING'", "cancelled"),
        ("payment_status='pending', status='PENDING'", "awaiting_payment"),
    ):
        guest.sql(f"UPDATE orders SET {statement} WHERE order_number=%s", (number,))  # noqa: S608
        assert await current() == expected, statement
    sized = await place(guest.client, [{"product_id": 505, "quantity": 1, "size": "L"}], "c" * 32)
    guest.sql(
        "UPDATE orders SET size_owner_state='uncertain', creation_token='t' WHERE order_number=%s",
        (sized.json()["order_number"],),
    )
    assert await current() == "awaiting_payment"


# --------------------------------------------------------------------------- CK-13


# --------------------------------------------------------------------------- CK-14


# --------------------------------------------------------------------------- CK-15


# --------------------------------------------------------------------------- CK-02, CK-16, CK-17


async def test_existing_channels_do_not_reach_or_leak_guest_orders(guest):
    created = await place(guest.client)
    number = created.json()["order_number"]
    guest_id = guest.sql("SELECT id FROM orders")[0][0]
    guest.sql(
        "INSERT INTO users (id,telegram_id,first_name,language_code,is_active,is_admin) "
        "VALUES (901,990901,'Admin','ru',true,true),(902,990902,'Buyer','ru',true,false)"
    )
    init = {"X-Init-Data": _make_init_data(BOT_TOKEN, {"id": 990902, "first_name": "Buyer"})}
    token = jwt.encode(
        {"sub": "901", "type": "admin", "exp": int(time.time()) + 600},
        config_module.settings.secret_key.get_secret_value(),
        algorithm=config_module.settings.algorithm,
    )
    admin = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(transport=ASGITransport(app=guest.app), base_url="http://test") as app:
        listing = await app.get("/api/v1/admin/orders", headers=admin)
        assert listing.status_code == 200
        [row] = listing.json()["items"]
        assert row["user_id"] is None
        assert row["order_number"] == number
        assert (row["delivery_name"], row["delivery_phone"], row["delivery_address"]) == (
            "G1 Buyer",
            "+998901234567",
            "Tashkent, synthetic street 1",
        )
        detail = await app.get(f"/api/v1/admin/orders/{guest_id}", headers=admin)
        assert detail.status_code == 200
        assert detail.json()["user_id"] is None
        own = await app.get("/api/v1/orders", headers=init)
        assert own.status_code == 200
        assert own.json()["items"] == []
        assert (await app.get(f"/api/v1/orders/{guest_id}", headers=init)).status_code == 403
        response = await app.post(
            "/api/v1/payments/create",
            json={"order_id": guest_id, "return_url": "https://evil.test"},
            headers=init,
        )
        assert response.status_code == 403
        # The purchase cookie authorizes none of the other channels.
        app.cookies.set(COOKIE, created.headers["set-cookie"].split(";")[0].split("=", 1)[1])
        for method, path in (
            ("GET", "/api/v1/orders"),
            ("GET", f"/api/v1/orders/{guest_id}"),
            ("POST", "/api/v1/payments/create"),
            ("GET", "/api/v1/admin/orders"),
        ):
            response = await app.request(
                method, path, json={"order_id": guest_id, "return_url": "x"}
            )
            assert response.status_code in (401, 403), path
    async with guest.browser() as client:
        client.cookies.set(COOKIE, created.headers["set-cookie"].split(";")[0].split("=", 1)[1])
        session = await client.get("/api/v1/buyer/auth/session")
        assert session.status_code == 404
        assert session.json() == {"error": "buyer_auth_disabled"}
    async with guest.browser() as client:
        for headers in (init, admin):
            response = await client.get(PATH + "/order", headers=headers)
            assert (response.status_code, response.json()) == (404, {"error": "order_not_found"})
    assert guest.provider.creates == []
    assert guest.messages == []


# --------------------------------------------------------------------------- CK-05


def test_cookie_is_filtered_from_any_route_event():
    result = filter_auth_telemetry(
        {
            "message": "PUBLIC_EVENT",
            "request": {
                "url": "https://shop.g1.test/api/v1/products",
                "cookies": {COOKIE: "SECRET_GUEST_COOKIE"},
                "headers": {"Cookie": f"{COOKIE}=SECRET_GUEST_COOKIE"},
            },
        },
        {},
    )
    assert result["message"] == "PUBLIC_EVENT"
    assert "SECRET_GUEST_COOKIE" not in str(result)
    assert filter_auth_telemetry({"request": {"url": ORIGIN + PATH + "/order"}}, {}) is None


async def test_status_read_has_no_side_effects(guest):
    created = await place(guest.client)
    before = guest.sql("SELECT row_to_json(orders)::text FROM orders")
    limits = guest.sql("SELECT key, events::text FROM buyer_auth_limits ORDER BY key")
    for _ in range(3):
        assert (await guest.client.get(PATH + "/order")).status_code == 200
    assert guest.sql("SELECT row_to_json(orders)::text FROM orders") == before
    assert guest.sql("SELECT key, events::text FROM buyer_auth_limits ORDER BY key") == limits
    assert guest.provider.creates == []
    assert created.status_code == 201
    async with guest.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Order)) == 1
        order = await db.scalar(select(Order))
        assert order.status == OrderStatus.PENDING
        assert await db.scalar(text("SELECT 1")) == 1
