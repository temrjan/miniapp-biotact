"""Mounted campaign proxy behavior with committed PostgreSQL authorization."""

import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import jwt
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import event, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.main import create_app
from src.users.models import User

URL = "/api/v1/admin/campaigns"
VERSION = "2026-09-30T07:00:00.123456Z"
SERVICE_TOKEN = "service-v3-test-only"
PRIVATE = "PRIVATE_V3_MARKER"
OPERATIONS = [
    ("GET", "", None, 200),
    ("GET", "/7", None, 200),
    ("POST", "", {"name": "Draft"}, 201),
    ("PATCH", "/7", {"expected_updated_at": VERSION, "image_url": None}, 200),
    ("POST", "/7/publish", {"expected_updated_at": VERSION}, 200),
    ("POST", "/7/end", {"expected_updated_at": VERSION}, 200),
]


def record(**changes: object) -> dict[str, object]:
    return {
        "id": 7,
        "name": "Draft",
        "product_id": None,
        "image_url": None,
        "description": None,
        "terms": None,
        "state": "draft",
        "start_at": None,
        "end_at": None,
        "published_at": None,
        "ended_at": None,
        "created_at": VERSION,
        "updated_at": VERSION,
    } | changes


def page() -> dict[str, object]:
    return {"items": [record()], "total": 1, "page": 1, "size": 20, "pages": 1}


def token(sub: str | None = "1", kind: str = "admin", *, expired: bool = False) -> str:
    payload = {"type": kind, "exp": int(time.time()) + (-60 if expired else 3600)}
    if sub is not None:
        payload["sub"] = sub
    return jwt.encode(payload, settings.secret_key.get_secret_value(), algorithm=settings.algorithm)


def headers(**kwargs: object) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(**kwargs)}"}


def assert_error(response: httpx.Response, status: int, code: str, message: str) -> None:
    assert response.status_code == status
    assert response.json() == {"detail": {"code": code, "message": message}}
    assert response.headers.get("cache-control") == "no-store"
    assert response.headers.get("www-authenticate") == ("Bearer" if status == 401 else None)


@dataclass
class Harness:
    app: FastAPI
    browser: httpx.AsyncClient
    portal: httpx.AsyncClient
    sessions: async_sessionmaker[AsyncSession]
    calls: list[httpx.Request]


@pytest_asyncio.fixture
async def harness(test_db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Harness]:
    monkeypatch.setattr(settings, "campaign_portal_origin", "https://portal.test")
    monkeypatch.setattr(settings, "campaign_service_token", SecretStr(SERVICE_TOKEN))
    engine = create_async_engine(settings.database_url, echo=False)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions.begin() as session:
        session.add(User(id=1, telegram_id=930001, is_admin=True, is_active=True))
    calls: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        listing = request.method == "GET" and request.url.path.endswith("/campaigns")
        return httpx.Response(
            201 if request.method == "POST" and request.url.path.endswith("/campaigns") else 200,
            json=page() if listing else record(),
        )

    app = create_app()
    app.state.campaign_sessions = sessions
    async with (
        httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False) as portal,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as browser,
    ):
        app.state.campaign_http_client = portal
        yield Harness(app, browser, portal, sessions, calls)
    await engine.dispose()


@pytest.mark.parametrize(("method", "suffix", "body", "success"), OPERATIONS)
async def test_every_operation_refuses_missing_auth(
    harness: Harness, method: str, suffix: str, body: dict[str, object] | None, success: int
) -> None:
    response = await harness.browser.request(method, URL + suffix, json=body)
    assert_error(response, 401, "invalid_admin_auth", "Войдите в административную панель заново.")
    assert harness.calls == []


@pytest.mark.parametrize(("method", "suffix", "body", "success"), OPERATIONS)
async def test_every_operation_accepts_current_admin(
    harness: Harness, method: str, suffix: str, body: dict[str, object] | None, success: int
) -> None:
    response = await harness.browser.request(method, URL + suffix, json=body, headers=headers())
    assert response.status_code == success
    assert response.json() == (page() if method == "GET" and not suffix else record())
    assert response.headers.get("cache-control") == "no-store"
    assert len(harness.calls) == 1
    assert harness.calls[0].method == method
    assert harness.calls[0].url == "https://portal.test/api/v1/internal/campaigns" + suffix + (
        "?page=1&size=20" if method == "GET" and not suffix else ""
    )
    if body is not None:
        sent = json.loads(harness.calls[0].content)
        if method != "POST" or suffix:
            assert sent == body
        else:
            assert sent == body | dict.fromkeys(
                ("product_id", "image_url", "description", "terms", "start_at", "end_at")
            )


@pytest.mark.parametrize(("method", "suffix", "body", "success"), OPERATIONS)
@pytest.mark.parametrize(
    ("case", "status", "code"),
    [
        ("wrong_scheme", 401, "invalid_admin_auth"),
        ("bad_signature", 401, "invalid_admin_auth"),
        ("expired", 401, "invalid_admin_auth"),
        ("malformed_subject", 401, "invalid_admin_auth"),
        ("wrong_type", 403, "admin_forbidden"),
        ("missing_subject", 403, "admin_forbidden"),
        ("deleted", 403, "admin_forbidden"),
        ("demoted", 403, "admin_forbidden"),
        ("inactive", 403, "admin_forbidden"),
    ],
)
async def test_every_operation_checks_auth_and_current_flags(
    harness: Harness,
    method: str,
    suffix: str,
    body: dict[str, object] | None,
    success: int,
    case: str,
    status: int,
    code: str,
) -> None:
    authorization = headers()
    if case == "wrong_scheme":
        authorization = {"Authorization": "tma " + PRIVATE, "X-Init-Data": PRIVATE}
    elif case == "bad_signature":
        authorization = {"Authorization": "Bearer " + PRIVATE}
    elif case == "expired":
        authorization = headers(expired=True)
    elif case == "malformed_subject":
        authorization = headers(sub=PRIVATE)
    elif case == "wrong_type":
        authorization = headers(kind="access")
    elif case == "missing_subject":
        authorization = headers(sub=None)
    elif case == "deleted":
        authorization = headers(sub="999")
    else:
        async with harness.sessions.begin() as session:
            await session.execute(
                update(User)
                .where(User.id == 1)
                .values(**{"is_admin" if case == "demoted" else "is_active": False})
            )
    response = await harness.browser.request(method, URL + suffix, json=body, headers=authorization)
    message = (
        "Войдите в административную панель заново."
        if status == 401
        else "Доступ к кампаниям разрешён только действующему администратору."
    )
    assert_error(response, status, code, message)
    assert PRIVATE not in response.text
    assert harness.calls == []


@pytest.mark.parametrize("flag", ["is_admin", "is_active"])
async def test_same_jwt_is_refused_on_next_request_after_revocation(
    harness: Harness, flag: str
) -> None:
    authorization = headers()
    assert (await harness.browser.get(URL, headers=authorization)).status_code == 200
    async with harness.sessions.begin() as session:
        await session.execute(update(User).where(User.id == 1).values(**{flag: False}))
    response = await harness.browser.get(URL, headers=authorization)
    assert_error(
        response,
        403,
        "admin_forbidden",
        "Доступ к кампаниям разрешён только действующему администратору.",
    )
    assert len(harness.calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"name": "Draft", "product_id": True},
        {"name": "Draft", "product_id": "7"},
        {"name": 7},
        {"name": "Draft", "private": PRIVATE},
        {"name": "Draft", "start_at": 123},
        {"name": "Draft", "start_at": "2026-09-30"},
        {"name": "Draft", "end_at": "2026-09-30T07:00:00"},
    ],
)
async def test_bad_body_is_redacted_before_io(
    harness: Harness, body: dict[str, object] | None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    response = await harness.browser.post(URL, json=body, headers=headers())
    assert_error(response, 422, "invalid_request", "Проверьте формат запроса и обязательные поля.")
    assert harness.calls == []
    assert PRIVATE not in response.text + caplog.text


@pytest.mark.parametrize(
    ("suffix", "body"),
    [
        ("/7", {}),
        ("/7", {"expected_updated_at": VERSION}),
        ("/7", {"expected_updated_at": VERSION, "name": None}),
        ("/7/publish", {"expected_updated_at": VERSION, "name": "Draft"}),
        ("/7/end", {"expected_updated_at": 123}),
    ],
)
async def test_invalid_edit_and_transition_are_local_refusals(
    harness: Harness, suffix: str, body: dict[str, object] | None
) -> None:
    response = await harness.browser.request(
        "PATCH" if suffix == "/7" else "POST", URL + suffix, json=body, headers=headers()
    )
    assert_error(response, 422, "invalid_request", "Проверьте формат запроса и обязательные поля.")
    assert harness.calls == []


async def test_auth_runs_before_malformed_body_or_id(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    for authorization, status, code, message in [
        ({}, 401, "invalid_admin_auth", "Войдите в административную панель заново."),
        (headers(), 422, "invalid_request", "Проверьте формат запроса и обязательные поля."),
    ]:
        response = await harness.browser.patch(
            URL + "/invalid",
            content="{" + PRIVATE,
            headers=authorization | {"Content-Type": "application/json"},
        )
        assert_error(response, status, code, message)
    assert harness.calls == []
    assert PRIVATE not in caplog.text


@pytest.mark.parametrize("query", ["state=active", "page=0", "size=101", "page=true", "size=x"])
async def test_documented_query_validation_is_redacted(harness: Harness, query: str) -> None:
    response = await harness.browser.get(URL + "?" + query, headers=headers())
    assert_error(response, 422, "invalid_request", "Проверьте формат запроса и обязательные поля.")
    assert harness.calls == []


async def test_unknown_query_and_browser_credentials_are_not_forwarded(harness: Harness) -> None:
    response = await harness.browser.get(
        URL + "?state=published&page=2&size=5&origin=" + PRIVATE,
        headers=headers() | {"Cookie": PRIVATE, "X-Init-Data": PRIVATE, "X-Private": PRIVATE},
    )
    assert response.status_code == 200
    request = harness.calls[0]
    assert dict(request.url.params) == {"state": "published", "page": "2", "size": "5"}
    assert request.headers["authorization"] == "Bearer " + SERVICE_TOKEN
    assert PRIVATE not in str(request.headers) + str(request.url) + request.content.decode()


@pytest.mark.parametrize(
    "config",
    [
        {"campaign_service_token": SecretStr("")},
        {"campaign_service_token": SecretStr(" ")},
        {"campaign_portal_origin": ""},
        {"campaign_portal_origin": "http://portal.test"},
        {"campaign_portal_origin": "https://portal.test/path"},
    ],
)
async def test_missing_or_invalid_config_is_definite_on_write(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, config: dict[str, object]
) -> None:
    for name, value in config.items():
        monkeypatch.setattr(settings, name, value)
    response = await harness.browser.post(URL, json={"name": "Draft"}, headers=headers())
    assert_error(
        response,
        503,
        "campaign_service_unavailable",
        "Сервис кампаний недоступен. Обратитесь к ответственному за систему.",
    )
    assert harness.calls == []


async def test_authorization_connection_is_released_before_network(harness: Harness) -> None:
    engine = harness.sessions.kw["bind"]
    checked_out = []

    def checkout(*args):
        checked_out.append(1)

    def checkin(*args):
        checked_out.pop()

    event.listen(engine.sync_engine, "checkout", checkout)
    event.listen(engine.sync_engine, "checkin", checkin)

    def respond(request):
        assert checked_out == []
        return httpx.Response(200, json=record())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as portal:
        harness.app.state.campaign_http_client = portal
        response = await harness.browser.get(URL + "/7", headers=headers())
    assert response.status_code == 200
    assert checked_out == []


async def test_database_failure_is_local_redacted_and_has_no_io(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    engine = harness.sessions.kw["bind"]

    def fail(*args):
        raise OperationalError("select", {}, Exception(PRIVATE))

    event.listen(engine.sync_engine, "before_cursor_execute", fail)
    response = await harness.browser.post(URL, json={"name": "Draft"}, headers=headers())
    event.remove(engine.sync_engine, "before_cursor_execute", fail)
    assert_error(
        response,
        503,
        "campaign_service_unavailable",
        "Сервис кампаний недоступен. Обратитесь к ответственному за систему.",
    )
    assert harness.calls == []
    assert PRIVATE not in response.text + caplog.text


@pytest.mark.parametrize("before_send", [False, True])
async def test_unexpected_failures_stay_inside_campaign_boundary(
    harness: Harness, caplog: pytest.LogCaptureFixture, before_send: bool
) -> None:
    from src.campaigns.dependencies import campaign_client

    caplog.set_level(logging.DEBUG)
    logging.getLogger("src.main").info("capture_control")

    def fail(*args):
        raise RuntimeError(PRIVATE)

    if before_send:
        harness.app.dependency_overrides[campaign_client] = lambda: fail()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as portal:
        harness.app.state.campaign_http_client = portal
        response = await harness.browser.post(URL, json={"name": "Draft"}, headers=headers())
    harness.app.dependency_overrides.clear()
    code = "campaign_service_unavailable" if before_send else "campaign_result_unknown"
    message = (
        "Сервис кампаний недоступен. Обратитесь к ответственному за систему."
        if before_send
        else "Не удалось подтвердить результат операции. Обновите данные кампаний перед повтором."
    )
    assert_error(response, 503, code, message)
    assert "capture_control" in caplog.text
    assert PRIVATE not in response.text + caplog.text


PORTAL_ERRORS = [
    (
        401,
        "invalid_service_auth",
        503,
        "campaign_service_unavailable",
        "Сервис кампаний недоступен. Обратитесь к ответственному за систему.",
    ),
    (404, "campaign_not_found", 404, "campaign_not_found", "Кампания не найдена. Обновите список."),
    (
        409,
        "campaign_changed",
        409,
        "campaign_changed",
        "Кампания изменена другим администратором. Обновите данные.",
    ),
    (
        409,
        "invalid_campaign_state",
        409,
        "invalid_campaign_state",
        "Действие недоступно в текущем состоянии кампании. Обновите данные.",
    ),
    (
        409,
        "immutable_campaign",
        409,
        "immutable_campaign",
        "После публикации кампания не редактируется. Завершите её и создайте новую.",
    ),
    (
        409,
        "campaign_overlap",
        409,
        "campaign_overlap",
        "Период пересекается с опубликованной кампанией этого товара.",
    ),
    (
        422,
        "invalid_request",
        422,
        "invalid_request",
        "Проверьте формат запроса и обязательные поля.",
    ),
    (
        422,
        "invalid_campaign_dates",
        422,
        "invalid_campaign_dates",
        "Проверьте начало и окончание периода кампании.",
    ),
    (
        422,
        "incomplete_campaign",
        422,
        "incomplete_campaign",
        "Перед публикацией заполните товар, изображение, описание, условия и даты.",
    ),
    (
        422,
        "invalid_image_url",
        422,
        "invalid_image_url",
        "Используйте изображение из загрузок магазина.",
    ),
    (422, "product_not_found", 422, "product_not_found", "Товар не найден. Выберите другой товар."),
    (422, "product_inactive", 422, "product_inactive", "Товар отключён в магазине."),
    (
        422,
        "product_not_purchasable",
        422,
        "product_not_purchasable",
        "Этот товар недоступен для покупки.",
    ),
    (
        422,
        "product_out_of_stock",
        422,
        "product_out_of_stock",
        "Товар отмечен как отсутствующий в наличии.",
    ),
    (
        503,
        "shop_unavailable",
        503,
        "shop_unavailable",
        "Не удалось проверить товар в магазине. Повторите позже.",
    ),
]


async def test_all_portal_refusals_have_exact_safe_envelope_and_no_store(harness: Harness) -> None:
    for upstream_status, upstream_code, local_status, local_code, message in PORTAL_ERRORS:

        def respond(request, status=upstream_status, code=upstream_code):
            harness.calls.append(request)
            return httpx.Response(
                status,
                json={"detail": {"code": code}},
                headers={"set-cookie": PRIVATE, "www-authenticate": PRIVATE},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as portal:
            harness.app.state.campaign_http_client = portal
            for method, suffix, body in [
                ("GET", "/7", None),
                ("POST", "/7/publish", {"expected_updated_at": VERSION}),
            ]:
                response = await harness.browser.request(
                    method, URL + suffix, headers=headers(), json=body
                )
                assert_error(response, local_status, local_code, message)
                assert "set-cookie" not in response.headers
                assert PRIVATE not in response.text + str(response.headers)
    assert len(harness.calls) == len(PORTAL_ERRORS) * 2


@pytest.mark.parametrize(("method", "suffix", "body", "success"), OPERATIONS)
async def test_b2_generic_503_depends_on_read_or_write(
    harness: Harness, method: str, suffix: str, body: dict[str, object] | None, success: int
) -> None:
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(503, json={"detail": {"code": "service_unavailable"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as portal:
        harness.app.state.campaign_http_client = portal
        response = await harness.browser.request(method, URL + suffix, headers=headers(), json=body)
    if method == "GET":
        assert_error(
            response,
            503,
            "service_unavailable",
            "Сервис кампаний временно недоступен. Повторите позже.",
        )
    else:
        assert_error(
            response,
            503,
            "campaign_result_unknown",
            "Не удалось подтвердить результат операции. Обновите данные кампаний перед повтором.",
        )
    assert len(calls) == 1


@pytest.mark.parametrize("lost_reply", [False, True])
async def test_b2_retained_draft_is_read_back_after_unconfirmed_create(
    harness: Harness, lost_reply: bool
) -> None:
    records = []
    calls = []

    def respond(request):
        calls.append(request)
        if request.method == "POST":
            records.append(record(name=json.loads(request.content)["name"]))
            if lost_reply:
                raise httpx.ReadError(PRIVATE)
            return httpx.Response(503, json={"detail": {"code": "service_unavailable"}})
        return httpx.Response(
            200, json={"items": records, "total": len(records), "page": 1, "size": 20, "pages": 1}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as portal:
        harness.app.state.campaign_http_client = portal
        response = await harness.browser.post(
            URL, headers=headers(), json={"name": "Retained Draft"}
        )
        assert_error(
            response,
            503,
            "campaign_result_unknown",
            "Не удалось подтвердить результат операции. Обновите данные кампаний перед повтором.",
        )
        fresh = await harness.browser.get(URL, headers=headers())
    assert fresh.status_code == 200
    assert fresh.json()["items"] == [record(name="Retained Draft")]
    assert [request.method for request in calls] == ["POST", "GET"]


async def test_immutable_image_and_transition_errors_keep_portal_precedence(
    harness: Harness,
) -> None:
    calls = []

    def respond(request):
        calls.append(request)
        code = "immutable_campaign" if request.method == "PATCH" else "invalid_campaign_state"
        if request.method == "GET":
            return httpx.Response(200, json=record(state="ended", ended_at=VERSION))
        return httpx.Response(409, json={"detail": {"code": code}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as portal:
        harness.app.state.campaign_http_client = portal
        stale = "2026-09-29T07:00:00.123456Z"
        image = {"expected_updated_at": stale, "image_url": "https://invalid.test/picture"}
        response = await harness.browser.patch(URL + "/7", headers=headers(), json=image)
        assert_error(
            response,
            409,
            "immutable_campaign",
            "После публикации кампания не редактируется. Завершите её и создайте новую.",
        )
        assert json.loads(calls[0].content) == image
        for action in ("publish", "end"):
            response = await harness.browser.post(
                URL + "/7/" + action, headers=headers(), json={"expected_updated_at": stale}
            )
            assert_error(
                response,
                409,
                "invalid_campaign_state",
                "Действие недоступно в текущем состоянии кампании. Обновите данные.",
            )
        history = await harness.browser.get(URL + "/7", headers=headers())
    assert history.json() == record(state="ended", ended_at=VERSION)
    assert [request.method for request in calls] == ["PATCH", "POST", "POST", "GET"]


@pytest.mark.parametrize("suffix", ["/0", "/-1", "/x"])
async def test_bad_campaign_id_is_local_refusal(harness: Harness, suffix: str) -> None:
    response = await harness.browser.get(URL + suffix, headers=headers())
    assert_error(response, 422, "invalid_request", "Проверьте формат запроса и обязательные поля.")
    assert harness.calls == []


async def test_unregistered_methods_and_trailing_slash_never_call_portal(harness: Harness) -> None:
    for method, suffix in [("DELETE", "/7"), ("PUT", "/7"), ("POST", "/7/anything"), ("GET", "/")]:
        response = await harness.browser.request(method, URL + suffix, headers=headers())
        assert response.status_code in (404, 405, 307)
    assert harness.calls == []


async def test_absent_config_lifespan_health_catalogue_and_checkout_are_independent(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from decimal import Decimal

    from src.database import get_db
    from src.main import health_check
    from src.notifications.service import telegram_notifier
    from src.products.models import Product
    from tests.conftest import _make_init_data
    from tests.test_orders_redeem import _payload

    monkeypatch.setattr(settings, "campaign_portal_origin", "")
    monkeypatch.setattr(settings, "campaign_service_token", SecretStr(""))
    monkeypatch.setattr(settings, "telegram_bot_token", "fake-v3-checkout-token")
    monkeypatch.setattr(settings, "loyalty_enabled", False)
    network_calls = []

    async def reject_network(_transport, request):
        network_calls.append(request)
        raise AssertionError("Unexpected external HTTP in startup or checkout")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", reject_network)

    async def no_notification(text, parse_mode="HTML"):
        return True

    monkeypatch.setattr(telegram_notifier, "send_message", no_notification)

    async def database():
        async with harness.sessions() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    harness.app.dependency_overrides[get_db] = database
    harness.app.add_api_route("/health", health_check)
    async with harness.sessions.begin() as session:
        product = Product(
            name="Synthetic Sale",
            slug="v3-sale",
            price=Decimal("200000"),
            sale_price=Decimal("150000"),
            is_sale=True,
            is_active=True,
            is_in_stock=True,
        )
        session.add(product)
        await session.flush()
        product_id = product.id
    authorization = {
        "X-Init-Data": _make_init_data(
            settings.telegram_bot_token, {"id": 930001, "first_name": "Synthetic"}
        )
    }
    async with harness.app.router.lifespan_context(harness.app):
        assert (await harness.browser.get("/health")).status_code == 200
        assert (await harness.browser.get("/api/v1/products")).status_code == 200
        read = await harness.browser.get(f"/api/v1/products/{product_id}")
        assert read.status_code == 200
        quote = await harness.browser.post(
            "/api/v1/orders/quote", headers=authorization, json=_payload(product_id)
        )
        assert quote.status_code == 200
        order = await harness.browser.post(
            "/api/v1/orders",
            headers=authorization,
            json=_payload(product_id) | {"quote_fingerprint": quote.json()["quote_fingerprint"]},
        )
        assert order.status_code == 201
        assert order.json()["total"] == quote.json()["total"]
        assert harness.calls == []
        assert network_calls == []
        client = harness.app.state.campaign_http_client
        assert client.is_closed is False
    assert client.is_closed is True
    async with harness.sessions() as session:
        saved = await session.get(Product, product_id)
        assert (
            saved.price,
            saved.sale_price,
            saved.is_sale,
            saved.is_in_stock,
            saved.is_active,
        ) == (Decimal("200000"), Decimal("150000"), True, True, True)


@pytest.mark.parametrize("origin", ["", "http://portal.test", "https://portal.test/path"])
def test_fresh_process_import_and_lifespan_accept_invalid_campaign_config(origin: str) -> None:
    script = """
import asyncio
import httpx
async def refuse_network(self, request):
    raise AssertionError('Unexpected startup HTTP request')
httpx.AsyncHTTPTransport.handle_async_request = refuse_network
from src.main import app
async def run():
    async with app.router.lifespan_context(app):
        assert not app.state.campaign_http_client.is_closed
    assert app.state.campaign_http_client.is_closed
asyncio.run(run())
print('fresh_import_lifespan=PASS')
"""
    result = subprocess.run(  # noqa: S603 — own interpreter and literal script, synthetic fixture env
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=os.environ | {"CAMPAIGN_PORTAL_ORIGIN": origin, "CAMPAIGN_SERVICE_TOKEN": ""},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "fresh_import_lifespan=PASS"
