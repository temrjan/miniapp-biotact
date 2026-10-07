"""T5: private auth errors/logs/late SDK capture, with live positive controls."""

import logging

import pytest
import sentry_sdk
from httpx import ASGITransport, AsyncClient
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from src.auth import admin_router as auth
from src.auth.passwords import DUMMY_HASH
from src.auth.privacy import filter_auth_telemetry
from src.config import settings
from src.main import create_app
from tests.test_admin_password_accounts import account, login
from tests.test_employee_referral_k1_receiver import Captured

PRIVATE = "SYNTHETIC_PRIVATE_AUTH_CANARY"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/login"),
        ("POST", "/login/"),
        ("GET", "/me"),
        ("GET", "/code/" + PRIVATE),
        ("OPTIONS", "/login"),
        ("GET", "/unknown/" + PRIVATE),
        ("GET", ""),
    ],
)
async def test_private_refusals_aliases_preflight_no_store(client, method, path):
    response = await client.request(
        method,
        "/api/v1/admin/auth" + path,
        headers={
            "Origin": settings.cors_origins[0],
            "Access-Control-Request-Method": "POST",
            "X-Bot-Token": PRIVATE,
            "X-Init-Data": PRIVATE,
            "Authorization": "Bearer " + PRIVATE,
        },
        content='{"password":"' + PRIVATE + '"}',
    )
    assert response.headers.get("cache-control") == "no-store"
    assert PRIVATE not in response.text


@pytest.mark.parametrize(
    "exception",
    [
        RuntimeError(PRIVATE),
        SQLAlchemyError(PRIVATE),
        OperationalError("secret SQL", {"password": PRIVATE}, RuntimeError(PRIVATE)),
        IntegrityError("secret SQL", {"hash": PRIVATE}, RuntimeError(PRIVATE)),
    ],
)
async def test_sql_runtime_failures_safe_response_logs(
    client, test_db, monkeypatch, caplog, exception
):
    auth.LOGIN_ATTEMPTS.clear()

    async def fail(*args, **kwargs):
        logging.getLogger("sqlalchemy.engine").error(
            "Password: %s hash: %s", PRIVATE, DUMMY_HASH, exc_info=exception
        )
        sentry_sdk.capture_exception(exception)
        raise exception

    monkeypatch.setattr(test_db, "scalar", fail)
    caplog.set_level(logging.DEBUG)
    response = await login(client, password=PRIVATE)
    assert response.status_code == 503
    assert response.json() == {"detail": "Authentication temporarily unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert PRIVATE not in caplog.text
    assert DUMMY_HASH not in caplog.text
    assert not auth.LOGIN_ATTEMPTS


async def test_bound_engine_echo_refuses_before_credential_sql(client, test_db, monkeypatch):
    auth.LOGIN_ATTEMPTS.clear()
    monkeypatch.setattr(test_db.get_bind().engine, "echo", True)

    def no_sql(*args, **kwargs):
        pytest.fail("echo-enabled credential SQL reached")

    monkeypatch.setattr(test_db, "scalar", no_sql)
    response = await login(client)
    assert response.status_code == 503
    assert not auth.LOGIN_ATTEMPTS


async def test_real_sdk_private_and_unrelated_positive_controls(test_db, monkeypatch, caplog):
    capture = Captured()
    with sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=capture,
        integrations=[FastApiIntegration(), SqlalchemyIntegration()],
        traces_sample_rate=1,
        before_send=filter_auth_telemetry,
        before_send_transaction=filter_auth_telemetry,
    ):
        app = create_app()
        from src.database import get_db

        async def session():
            yield test_db

        app.dependency_overrides[get_db] = session

        @app.get("/api/v1/admin/auth/private-failure")
        async def failure():
            logging.getLogger("test-auth").error(PRIVATE)
            raise RuntimeError(PRIVATE)

        @app.get("/public-positive")
        async def positive():
            logging.getLogger("test-auth").error("Public telemetry control")
            sentry_sdk.capture_message("Public telemetry control")
            return {"ok": True}

        caplog.set_level(logging.INFO)
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="https://test"
        ) as browser:
            response = await browser.get(
                "/api/v1/admin/auth/private-failure", headers={"X-Bot-Token": PRIVATE}
            )
            assert response.status_code == 503
            assert PRIVATE not in response.text
            await browser.get("/api/v1/admin/auth/code/" + PRIVATE)
            assert not capture.events
            logging.getLogger("uvicorn.access").info(
                '%s - "%s %s HTTP/%s" %s',
                "client",
                "GET",
                "/api/v1/admin/auth/code/" + PRIVATE,
                "1.1",
                200,
            )
            assert PRIVATE not in caplog.text
            await browser.get("/public-positive")
        sentry_sdk.flush()
        assert capture.events
        assert "Public telemetry control" in str(capture.events)
        assert PRIVATE not in str(capture.events)
        assert "Public telemetry control" in caplog.text
    assert (
        filter_auth_telemetry(
            {"request": {"url": "https://test/api/v1/admin/auth/code/" + PRIVATE}}, {}
        )
        is None
    )
    assert filter_auth_telemetry({"tags": {"biotact_admin_auth": "yes"}}, {}) is None
    unrelated = {"message": "public-positive"}
    assert filter_auth_telemetry(unrelated, {}) == unrelated


async def test_success_token_is_only_intended_response_not_logs(client, test_db, caplog):
    auth.LOGIN_ATTEMPTS.clear()
    await account(test_db)
    caplog.set_level(logging.DEBUG)
    response = await login(client)
    assert response.status_code == 200
    assert response.json()["access_token"] not in caplog.text
