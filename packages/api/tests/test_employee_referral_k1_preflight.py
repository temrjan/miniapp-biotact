"""F-02: classify K1 before real CORS early responses and SDK exception capture."""

import asyncio
import json

import pytest
import sentry_sdk
from httpx import ASGITransport, AsyncClient
from sentry_sdk.integrations.fastapi import FastApiIntegration

from src.config import settings
from src.employee_referrals.privacy import filter_k1
from src.main import create_app
from tests.test_employee_referral_k1_contract import signed
from tests.test_employee_referral_k1_receiver import Captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sample_rate", [None, 0.0, 1.0], ids=["no-tracing", "unsampled", "sampled"]
)
@pytest.mark.parametrize("enabled", [False, True], ids=["off", "on"])
@pytest.mark.parametrize("allowed", [False, True], ids=["denied-origin", "allowed-origin"])
@pytest.mark.parametrize("fail_send", [False, True], ids=["success", "send-failure"])
@pytest.mark.parametrize("alternate", [False, True], ids=["canonical", "custom-encoded-host"])
async def test_preflight_preserves_cors_without_disclosing_k1_headers(
    monkeypatch, caplog, sample_rate, enabled, allowed, fail_send, alternate
):
    prefix = "/custom" if alternate else "/api/v1"
    path = prefix + "/internal/employee-referrals/k1"
    wire_path = path.replace("/k1", "/%6b1") if alternate else path
    monkeypatch.setattr(settings, "api_v1_prefix", prefix)
    monkeypatch.setattr(settings, "employee_referral_k1_enabled", enabled)
    monkeypatch.setattr(settings, "cors_origins", ["https://allowed.invalid"])
    capture = Captured()
    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=capture,
        integrations=[FastApiIntegration()],
        traces_sample_rate=sample_rate,
        before_send=filter_k1,
        before_send_transaction=filter_k1,
    )
    app = create_app()
    marker = "SYNTHETIC_F02_PRIVATE_HEADER"
    reads = 0
    statuses = []

    @app.get("/ordinary-f02-control")
    async def ordinary():
        sentry_sdk.capture_message("ORDINARY_F02_CONTROL")
        return {"ok": True}

    async def wrapped(scope, receive, send):
        async def counted():
            nonlocal reads
            reads += 1
            return await receive()

        async def observed_send(message):
            if scope["path"] == path:
                if message["type"] == "http.response.start":
                    statuses.append(message["status"])
                if fail_send:
                    raise ConnectionError("synthetic preflight send failure")
            await send(message)

        await app(scope, counted, observed_send)

    headers = signed(b"{}", path=wire_path) | {
        "host": "test/other?x=" if alternate else "test",
        "origin": "https://allowed.invalid" if allowed else "https://denied.invalid",
        "access-control-request-method": "POST",
        "access-control-request-headers": "x-biotact-k1-signature",
        "x-f02-private": marker,
    }
    try:
        async with AsyncClient(
            transport=ASGITransport(app=wrapped), base_url="http://test"
        ) as client:
            if fail_send:
                with pytest.raises(ConnectionError, match="synthetic preflight send failure"):
                    await client.options(wire_path, headers=headers)
            else:
                response = await client.options(wire_path, headers=headers)
                assert response.status_code == (200 if allowed else 400)
                assert response.text == ("OK" if allowed else "Disallowed CORS origin")
                assert response.headers.get("access-control-allow-origin") == (
                    "https://allowed.invalid" if allowed else None
                )
                assert response.headers["access-control-allow-headers"] == "x-biotact-k1-signature"
            assert statuses[0] == (200 if allowed else 400)
            assert reads == 0
            sentry_sdk.flush()
            private = list(capture.events)
            capture.events.clear()
            # A Host-derived URL that resembles K1 must not suppress this ordinary path.
            assert (
                await client.get("/ordinary-f02-control", headers={"host": "test" + path + "?"})
            ).status_code == 200
            sentry_sdk.flush()
            assert any(e.get("message") == "ORDINARY_F02_CONTROL" for e in capture.events)
            assert sum(e.get("type") == "transaction" for e in capture.events) == (
                1 if sample_rate == 1.0 else 0
            )
            assert not private, "F-02: early K1 response reached SDK capture"
            assert marker not in json.dumps(capture.events)
            assert marker not in caplog.text
    finally:
        sentry_sdk.get_client().close()


@pytest.mark.asyncio
@pytest.mark.parametrize("sample_rate", [None, 1.0], ids=["no-tracing", "sampled"])
async def test_overlapping_preflight_and_ordinary_request_keep_scopes_isolated(
    monkeypatch, sample_rate
):
    monkeypatch.setattr(settings, "employee_referral_k1_enabled", False)
    capture = Captured()
    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=capture,
        integrations=[FastApiIntegration()],
        traces_sample_rate=sample_rate,
        before_send=filter_k1,
        before_send_transaction=filter_k1,
    )
    app = create_app()
    path = settings.api_v1_prefix + "/internal/employee-referrals/k1"
    sending = asyncio.Event()
    ordinary_done = asyncio.Event()

    @app.get("/overlapping-f02-control")
    async def ordinary():
        await sending.wait()
        sentry_sdk.capture_message("OVERLAPPING_F02_CONTROL")
        ordinary_done.set()
        return {"ok": True}

    async def wrapped(scope, receive, send):
        async def failed_send(message):
            sending.set()
            await ordinary_done.wait()
            raise ConnectionError("synthetic overlapping send failure")

        await app(scope, receive, failed_send if scope["path"] == path else send)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=wrapped), base_url="http://test"
        ) as client:

            async def private_request():
                with pytest.raises(ConnectionError):
                    await client.options(
                        path,
                        headers={
                            "origin": "https://other.invalid",
                            "access-control-request-method": "POST",
                            "x-f02-private": "SYNTHETIC_F02_OVERLAP",
                        },
                    )

            async with asyncio.timeout(10):
                _, response = await asyncio.gather(
                    private_request(), client.get("/overlapping-f02-control")
                )
            assert response.status_code == 200
            sentry_sdk.flush()
            assert [e.get("message") for e in capture.events if "message" in e] == [
                "OVERLAPPING_F02_CONTROL"
            ]
            assert len(capture.events) == (2 if sample_rate == 1.0 else 1)
            assert "SYNTHETIC_F02_OVERLAP" not in json.dumps(capture.events)
    finally:
        sentry_sdk.get_client().close()
