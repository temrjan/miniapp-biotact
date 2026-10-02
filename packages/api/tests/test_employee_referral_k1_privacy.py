"""Real SDK regression for F-01: Host must not determine K1 privacy."""

import json

import pytest
import sentry_sdk
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sentry_sdk.integrations.fastapi import FastApiIntegration

from src.config import settings
from src.employee_referrals import router
from src.employee_referrals.privacy import filter_k1
from src.main import create_app
from tests.test_employee_referral_k1_contract import KEY, signed
from tests.test_employee_referral_k1_receiver import Captured


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["test", "test/other", "test/other?x=", "test#fragment"])
@pytest.mark.parametrize("prefix", ["/api/v1", "/custom"])
@pytest.mark.parametrize("encoded_path", [False, True], ids=["canonical", "encoded"])
@pytest.mark.parametrize("status", [404, 401, 503, 422, 405])
async def test_host_cannot_expose_k1_events_or_transaction(
    monkeypatch, host, prefix, encoded_path, status
):
    monkeypatch.setattr(settings, "api_v1_prefix", prefix)
    monkeypatch.setattr(settings, "employee_referral_k1_enabled", status != 404)
    monkeypatch.setattr(
        settings, "employee_referral_k1_key", SecretStr("" if status == 503 else KEY.hex())
    )
    capture = Captured()
    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=capture,
        integrations=[FastApiIntegration()],
        traces_sample_rate=1.0,
        before_send=filter_k1,
        before_send_transaction=filter_k1,
    )
    marker = "SYNTHETIC_F01_PRIVATE_HEADER"
    original_decode = router.decode_key

    def observed_decode(value):
        # Exercise event capture inside the real boundary without replacing validation.
        sentry_sdk.capture_message(marker)
        return original_decode(value)

    monkeypatch.setattr(router, "decode_key", observed_decode)
    app = create_app()

    @app.get("/ordinary-f01-control")
    async def ordinary():
        sentry_sdk.capture_message("ORDINARY_F01_CONTROL")
        return {"ok": True}

    reads = 0

    async def measured(scope, receive, send):
        async def counted():
            nonlocal reads
            reads += 1
            return await receive()

        await app(scope, counted, send)

    path = prefix + "/internal/employee-referrals/" + ("%6b1" if encoded_path else "k1")
    body = b"{}"
    headers = signed(body, path=path) | {"host": host, "x-f01-private": marker}
    if status == 401:
        headers["x-biotact-k1-signature"] = "invalid"
    try:
        async with AsyncClient(
            transport=ASGITransport(app=measured), base_url="http://test"
        ) as client:
            response = await client.request(
                "GET" if status == 405 else "POST", path, content=body, headers=headers
            )
            assert response.status_code == status
            if status != 422:
                assert reads == 0, "Early refusal must precede SDK body reads"
            sentry_sdk.flush()
            private_events = list(capture.events)
            # Same-task ordinary requests must still emit both event and transaction,
            # even when their Host makes an SDK URL look like the K1 path.
            capture.events.clear()
            for ordinary_host in (host, "test" + prefix + "/internal/employee-referrals/k1?"):
                assert (
                    await client.get("/ordinary-f01-control", headers={"host": ordinary_host})
                ).status_code == 200
            sentry_sdk.flush()
            assert sum(e.get("type") == "transaction" for e in capture.events) == 2
            assert sum(e.get("message") == "ORDINARY_F01_CONTROL" for e in capture.events) == 2
            serialized = json.dumps(private_events)
            assert marker not in serialized, "Private K1 marker reached SDK transport"
            assert headers["x-biotact-k1-signature"] not in serialized
            assert not private_events, "K1 events and completed request transaction must be dropped"
    finally:
        sentry_sdk.get_client().close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sample_rate", [None, 0.0, 1.0], ids=["no-tracing", "unsampled", "sampled"]
)
@pytest.mark.parametrize("enabled", [False, True], ids=["disabled", "unauthenticated"])
async def test_send_failure_after_route_exit_cannot_expose_private_headers(
    monkeypatch, caplog, sample_rate, enabled
):
    monkeypatch.setattr(settings, "employee_referral_k1_enabled", enabled)
    monkeypatch.setattr(settings, "employee_referral_k1_key", SecretStr(KEY.hex()))
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
    marker = "SYNTHETIC_F01_SEND_PRIVATE_HEADER"

    @app.get("/ordinary-send-control")
    async def ordinary():
        sentry_sdk.capture_message("ORDINARY_SEND_CONTROL")
        return {"ok": True}

    async def broken_transport(scope, receive, send):
        async def fail_send(message):
            raise ConnectionError("synthetic response transport failure")

        await app(scope, receive, fail_send if scope["path"] == path else send)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=broken_transport), base_url="http://test"
        ) as client:
            with pytest.raises(ConnectionError, match="synthetic response transport failure"):
                await client.post(
                    path, content=b"{}", headers={"host": "test/other", "x-f01-private": marker}
                )
            sentry_sdk.flush()
            private_events = list(capture.events)
            capture.events.clear()
            assert (await client.get("/ordinary-send-control")).status_code == 200
            sentry_sdk.flush()
            assert any(e.get("message") == "ORDINARY_SEND_CONTROL" for e in capture.events)
            assert sum(e.get("type") == "transaction" for e in capture.events) == (
                1 if sample_rate == 1.0 else 0
            )
            assert marker not in json.dumps(private_events)
            assert not private_events, "Late SDK exception capture must retain the request tag"
            assert marker not in caplog.text
    finally:
        sentry_sdk.get_client().close()


@pytest.mark.asyncio
async def test_sdk_without_asgi_integration_does_not_tag_the_callers_scope(monkeypatch):
    monkeypatch.setattr(settings, "employee_referral_k1_enabled", True)
    monkeypatch.setattr(settings, "employee_referral_k1_key", SecretStr(KEY.hex()))
    capture = Captured()
    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=capture,
        default_integrations=False,
        before_send=filter_k1,
        before_send_transaction=filter_k1,
    )
    original_decode = router.decode_key

    def observed_decode(value):
        sentry_sdk.capture_message("SYNTHETIC_F01_WITHOUT_ASGI")
        return original_decode(value)

    monkeypatch.setattr(router, "decode_key", observed_decode)
    path = settings.api_v1_prefix + "/internal/employee-referrals/k1"
    try:
        async with AsyncClient(
            transport=ASGITransport(app=create_app()), base_url="http://test"
        ) as client:
            assert (
                await client.post(path, content=b"{}", headers=signed(b"{}", path=path))
            ).status_code == 422
        sentry_sdk.capture_message("ORDINARY_WITHOUT_ASGI")
        sentry_sdk.flush()
        assert [e.get("message") for e in capture.events] == ["ORDINARY_WITHOUT_ASGI"]
    finally:
        sentry_sdk.get_client().close()
