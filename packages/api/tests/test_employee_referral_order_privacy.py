"""Real SDK/log controls and unchanged shopper access at order boundaries."""

import json

import pytest
import sentry_sdk
from httpx import ASGITransport, AsyncClient
from sentry_sdk.integrations.fastapi import FastApiIntegration

from src.config import settings
from src.employee_referrals import capture
from src.employee_referrals.order_privacy import filter_referral_telemetry
from src.main import create_app
from src.users.models import User
from tests.conftest import _make_init_data
from tests.test_employee_referral_k1_receiver import Captured
from tests.test_employee_referral_order_capture import TOKEN
from tests.test_employee_referral_order_capture import checkout as checkout


@pytest.mark.parametrize("host", ["test", "test/other?x="])
@pytest.mark.parametrize(
    "case", ["valid", "validation", "auth", "config", "duplicate", "exception", "debug", "quote"]
)
async def test_private_input_never_reaches_response_logger_or_sdk(
    checkout, monkeypatch, caplog, host, case
):
    h = checkout
    collected = Captured()
    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=collected,
        integrations=[FastApiIntegration()],
        traces_sample_rate=1.0,
        before_send=filter_referral_telemetry,
        before_send_transaction=filter_referral_telemetry,
    )
    marker = "SYNTHETIC_CAPTURE_PRIVATE_MARKER"
    original = capture.store_snapshot

    async def observed(*args):
        sentry_sdk.capture_message(marker + TOKEN)
        await original(*args)
        if case == "duplicate":
            await original(*args)
        elif case in {"exception", "debug"}:
            raise RuntimeError(marker + TOKEN)

    monkeypatch.setattr(capture, "store_snapshot", observed)
    monkeypatch.setattr(settings, "debug", case == "debug")
    monkeypatch.setattr(settings, "database_echo", case == "config")
    app = create_app()
    app.state.employee_referral_sessions = h.sessions

    @app.get("/capture-ordinary-control")
    async def ordinary():
        sentry_sdk.capture_message("ORDINARY_CAPTURE_CONTROL")
        return {"ok": True}

    body = h.body()
    if case in {"validation", "quote"}:
        body["referral_token"] = {"private": marker + TOKEN}
    headers = {"host": host, "x-capture-private": marker}
    if case != "auth":
        headers.update(h.headers)
    path = "/api/v1/orders" + ("/quote" if case == "quote" else "")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(path, json=body, headers=headers)
            assert (
                response.status_code
                == {
                    "valid": 201,
                    "validation": 422,
                    "auth": 401,
                    "config": 503,
                    "duplicate": 409,
                    "exception": 500,
                    "debug": 500,
                    "quote": 422,
                }[case]
            )
            for value in (
                marker,
                TOKEN,
                h.data["campaign"]["name"],
                h.data["participants"][0]["employee_display_name"],
            ):
                assert value not in response.text
                assert value not in caplog.text
            sentry_sdk.flush()
            assert not collected.events, json.dumps(collected.events)
            assert (
                await client.get(
                    "/capture-ordinary-control", headers={"host": "test/api/v1/orders?"}
                )
            ).status_code == 200
            sentry_sdk.flush()
            assert any(e.get("message") == "ORDINARY_CAPTURE_CONTROL" for e in collected.events)
            assert any(e.get("type") == "transaction" for e in collected.events)
    finally:
        sentry_sdk.get_client().close()


@pytest.mark.parametrize("path", ["/api/v1/orders", "/api/v1/orders/quote"])
async def test_late_send_failure_retains_sdk_privacy_tag(checkout, path):
    h = checkout
    collected = Captured()
    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=collected,
        integrations=[FastApiIntegration()],
        traces_sample_rate=1.0,
        before_send=filter_referral_telemetry,
        before_send_transaction=filter_referral_telemetry,
    )

    async def broken(scope, receive, send):
        async def fail_send(_message):
            raise ConnectionError("synthetic late send failure")

        await h.app(scope, receive, fail_send)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=broken), base_url="http://test"
        ) as client:
            with pytest.raises(ConnectionError, match="late send failure"):
                await client.post(
                    path,
                    json=h.body(),
                    headers=h.headers | {"host": "test/other", "x-capture-private": TOKEN},
                )
        sentry_sdk.flush()
        assert not collected.events
    finally:
        sentry_sdk.get_client().close()


async def test_no_asgi_sdk_integration_does_not_tag_ordinary_caller(checkout):
    h = checkout
    collected = Captured()
    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=collected,
        default_integrations=False,
        before_send=filter_referral_telemetry,
        before_send_transaction=filter_referral_telemetry,
    )
    try:
        assert (await h.create()).status_code == 201
        sentry_sdk.capture_message("ORDINARY_CALLER_SCOPE")
        sentry_sdk.flush()
        assert [e.get("message") for e in collected.events] == ["ORDINARY_CALLER_SCOPE"]
    finally:
        sentry_sdk.get_client().close()


async def test_token_does_not_authorize_another_user_or_inactive_shopper(checkout):
    h = checkout
    order_id = (await h.create()).json()["id"]
    assert (await h.client.post("/api/v1/orders", json=h.body())).status_code == 401
    async with h.sessions.begin() as writer:
        writer.add(User(id=2, telegram_id=990040, first_name="Other shopper"))
        writer.add(User(id=3, telegram_id=990041, first_name="Inactive", is_active=False))
    other = {
        "X-Init-Data": _make_init_data(
            settings.telegram_bot_token, {"id": 990040, "first_name": "Other shopper"}
        )
    }
    inactive = {
        "X-Init-Data": _make_init_data(
            settings.telegram_bot_token, {"id": 990041, "first_name": "Inactive"}
        )
    }
    assert (await h.client.get(f"/api/v1/orders/{order_id}", headers=other)).status_code == 403
    assert (
        await h.client.post("/api/v1/orders", json=h.body(), headers=inactive)
    ).status_code == 403
