"""Mounted real auth/wire, real PostgreSQL K1 and no-write/locking/privacy evidence."""

import asyncio
import json
import logging
from datetime import timedelta

import pytest
import sentry_sdk
from httpx import ASGITransport, AsyncClient
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sqlalchemy import event, func, select, text

from src.auth import dependencies
from src.config import settings
from src.employee_referrals import capture, entry
from src.employee_referrals.models import CampaignReplica, ReferralIdentity
from src.employee_referrals.order_privacy import filter_referral_telemetry
from src.main import create_app
from src.users.models import User
from tests.conftest import _make_init_data
from tests.test_employee_referral_k1_receiver import Captured
from tests.test_employee_referral_order_capture import TOKEN, checkout as checkout


async def wait_for_lock(h):
    async with h.sessions() as observer:
        for _ in range(300):
            waiting = await observer.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND cardinality(pg_blocking_pids(pid))>0"
                )
            )
            await observer.commit()  # refresh pg_stat_activity's per-transaction snapshot
            if waiting:
                return
            await asyncio.sleep(0.01)
    raise AssertionError("Expected a real PostgreSQL lock waiter")


PATH = "/api/v1/employee-referrals/resolve"


@pytest.fixture(autouse=True)
def entry_enabled(monkeypatch):
    monkeypatch.setattr(settings, "employee_referral_entry_enabled", True)


async def resolve(h, **kwargs):
    return await h.client.post(PATH, json={"token": TOKEN}, headers=h.headers, **kwargs)


async def test_valid_minimum_dto_no_writes_and_capture_rechecks(checkout):
    h = checkout
    before = await h.state()
    response = await resolve(h)
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"state", "product_id", "observed_at", "expires_at"}
    assert data["state"] == "valid" and data["product_id"] == "39"
    assert data["observed_at"].endswith("Z") and len(data["observed_at"]) == 27
    assert response.headers["cache-control"] == "no-store"
    assert await h.state() == before
    async with h.sessions.begin() as writer:
        campaign = await writer.get(CampaignReplica, 1)
        campaign.end_at = h.now
    response = await h.create()
    assert response.status_code == 201
    assert (await h.state())[1:3] == (0, 0)


@pytest.mark.parametrize(
    "flag", ["employee_referral_entry_enabled", "employee_referral_capture_enabled"]
)
async def test_off_before_body_auth_and_sql(checkout, monkeypatch, flag):
    monkeypatch.setattr(settings, flag, False)
    statements = []
    event.listen(
        checkout.engine.sync_engine,
        "before_cursor_execute",
        lambda *args: statements.append(args[2]),
    )
    response = await checkout.client.post(PATH, content=b"not json")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "referral_entry_disabled"
    assert not statements


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"{}", 422),
        (b'{"token":null}', 422),
        (b'{"token":1}', 422),
        (b'{"token":true}', 422),
        (b'{"token":[]}', 422),
        (b'{"token":{}}', 422),
        (b'{"token":"x","extra":1}', 422),
        (b'{"token":"a","token":"b"}', 422),
        (b"[]", 422),
        (b"null", 422),
        (b"\xff", 422),
        (b'{"token":"' + b"a" * 257 + b'"}', 422),
        (b"x" * 1025, 413),
        (b'{"token":""}', 200),
        (b'{"token":"AAAAAAAAAAAAAAAAAAAAAB"}', 200),
        (b'{"token":"AQEBAQEBAQEBAQEBAQEBAQ"}', 200),
    ],
)
async def test_bounded_wire_and_uniform_invalid(checkout, raw, expected):
    response = await checkout.client.post(
        PATH, content=raw, headers=checkout.headers | {"content-type": "application/json"}
    )
    assert response.status_code == expected, response.text
    assert response.headers["cache-control"] == "no-store"
    if expected == 200:
        assert response.json() == {"state": "invalid"}
    else:
        assert "input" not in response.text and raw.decode(errors="replace") not in response.text


@pytest.mark.parametrize(
    "headers",
    [
        {"content-type": "text/plain"},
        {"content-type": "application/json", "content-encoding": "gzip"},
    ],
)
async def test_media(checkout, headers):
    r = await checkout.client.post(PATH, content=b"{}", headers=checkout.headers | headers)
    assert r.status_code == 415


async def test_stream_cap_ignores_false_content_length(checkout):
    async def stream():
        yield b"a" * 700
        yield b"b" * 400

    r = await checkout.client.post(
        PATH,
        content=stream(),
        headers=checkout.headers | {"content-type": "application/json", "content-length": "2"},
    )
    assert r.status_code == 413


async def test_malformed_has_no_token_sql(checkout):
    statements = []

    def observe(*args):
        statements.append(args[2])

    event.listen(checkout.engine.sync_engine, "before_cursor_execute", observe)
    try:
        r = await checkout.client.post(PATH, json={"token": "!"}, headers=checkout.headers)
    finally:
        event.remove(checkout.engine.sync_engine, "before_cursor_execute", observe)
    assert r.json() == {"state": "invalid"}
    assert not any("employee_referral_" in s for s in statements)


@pytest.mark.parametrize(
    "case",
    [
        "start",
        "before-start",
        "end",
        "expiry",
        "future-issued",
        "future-published",
        "removed",
        "ended",
    ],
)
async def test_exact_registry_times(checkout, monkeypatch, case):
    h = checkout
    now = h.now
    async with h.sessions.begin() as writer:
        c = await writer.get(CampaignReplica, 1)
        i = await writer.get(ReferralIdentity, TOKEN)
        if case == "start":
            c.start_at = now
        elif case == "before-start":
            c.start_at = now + timedelta(microseconds=1)
        elif case == "end":
            c.end_at = now
        elif case == "expiry":
            c.issued_at = now - timedelta(seconds=60)
            c.valid_until = now
        elif case == "future-issued":
            c.issued_at = now + timedelta(seconds=1)
            c.valid_until = c.issued_at + timedelta(seconds=60)
        elif case == "future-published":
            c.published_at = now + timedelta(microseconds=1)
        elif case == "removed":
            i.in_current_registry = False
        else:
            c.state = "ended"
            c.ended_at = now

    async def clock(_session):
        return now

    monkeypatch.setattr(capture, "capture_clock", clock)
    assert (await resolve(h)).json()["state"] == ("valid" if case == "start" else "invalid")


@pytest.mark.parametrize("change", ["end", "removed", "expiry"])
async def test_waiter_refreshes_and_uses_db_clock_after_lock(checkout, change):
    h = checkout
    async with h.sessions() as writer:
        c = await writer.scalar(
            select(CampaignReplica).where(CampaignReplica.campaign_id == 1).with_for_update()
        )
        now = await writer.scalar(select(func.clock_timestamp()))
        if change == "end":
            c.state = "ended"
            c.ended_at = now
        elif change == "removed":
            (await writer.get(ReferralIdentity, TOKEN)).in_current_registry = False
        else:
            c.valid_until = now + timedelta(milliseconds=200)
            c.issued_at = c.valid_until - timedelta(seconds=60)
        await writer.flush()
        pending = asyncio.create_task(resolve(h))
        try:
            await wait_for_lock(h)
            if change == "expiry":
                while await writer.scalar(select(func.clock_timestamp())) < c.valid_until:
                    await asyncio.sleep(0.01)
            await writer.commit()
            assert (await asyncio.wait_for(pending, 3)).json() == {"state": "invalid"}
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


async def test_stale_identity_map_and_cancel_release_lock(checkout):
    h = checkout
    async with h.sessions() as reader:
        identity = await reader.get(ReferralIdentity, TOKEN)
        async with h.sessions.begin() as writer:
            (await writer.get(ReferralIdentity, TOKEN)).in_current_registry = False
        assert await capture.current_referral(reader, TOKEN) is None
        assert not identity.in_current_registry
    async with h.sessions() as writer:
        await writer.scalar(select(CampaignReplica).with_for_update())
        pending = asyncio.create_task(resolve(h))
        await wait_for_lock(h)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await writer.rollback()
    assert (await resolve(h)).json() == {"state": "invalid"}


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "invalid",
        "signed-rejected",
        "inactive",
        "error",
        "debug",
        "echo",
        "actual-echo",
        "maker-echo",
    ],
)
async def test_auth_and_error_privacy_with_active_controls(checkout, monkeypatch, caplog, case):
    h = checkout
    marker = "SYNTHETIC_ENTRY_CANARY_" + TOKEN
    collected = Captured()
    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=collected,
        integrations=[FastApiIntegration()],
        traces_sample_rate=1.0,
        before_send=filter_referral_telemetry,
        before_send_transaction=filter_referral_telemetry,
    )
    headers = h.headers
    if case == "missing":
        headers = {}
    elif case == "invalid":
        headers = {"X-Init-Data": marker}
    elif case == "signed-rejected":
        headers = {
            "X-Init-Data": _make_init_data(
                settings.telegram_bot_token, {"id": "bad-" + marker, "first_name": marker}
            )
        }
    elif case == "inactive":
        async with h.sessions.begin() as writer:
            (await writer.get(User, 1)).is_active = False
    if case == "signed-rejected":
        actual = dependencies.validate_init_data

        def observe_auth(*args, **kwargs):
            logging.getLogger("entry-dependency-probe").error(marker)
            sentry_sdk.capture_message(marker)
            return actual(*args, **kwargs)

        monkeypatch.setattr(dependencies, "validate_init_data", observe_auth)
    if case in {"error", "debug"}:

        async def fail(*args):
            logging.getLogger("entry-probe").error(marker)
            sentry_sdk.capture_message(marker)
            raise RuntimeError(marker)

        monkeypatch.setattr(entry, "current_referral", fail)
        monkeypatch.setattr(settings, "debug", case == "debug")
    elif case == "echo":
        monkeypatch.setattr(settings, "database_echo", True)
    elif case == "actual-echo":
        monkeypatch.setattr(entry.engine, "echo", True)
    elif case == "maker-echo":
        monkeypatch.setattr(h.engine, "echo", True)
    app = create_app()
    app.state.employee_referral_sessions = h.sessions

    @app.get("/entry-control")
    async def control():
        logging.getLogger("entry-probe").warning("ENTRY_ORDINARY_CONTROL")
        sentry_sdk.capture_message("ENTRY_ORDINARY_CONTROL")
        return {"ok": True}

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r = await client.post(PATH, json={"token": TOKEN}, headers=headers)
            expected = (
                401
                if case in {"missing", "invalid", "signed-rejected"}
                else 403
                if case == "inactive"
                else 503
            )
            assert r.status_code == expected, r.text
            assert r.headers["cache-control"] == "no-store"
            if expected == 401:
                assert r.json() == {
                    "detail": {
                        "code": "referral_entry_invalid_auth",
                        "message": "Invalid referral entry authentication",
                    }
                }
                assert r.headers["www-authenticate"] == "Telegram-Init-Data"
            if expected == 403:
                assert r.json() == {
                    "detail": {
                        "code": "referral_entry_forbidden",
                        "message": "Referral entry access denied",
                    }
                }
                assert "www-authenticate" not in r.headers
            assert marker not in r.text and TOKEN not in r.text and marker not in caplog.text
            sentry_sdk.flush()
            assert not collected.events
            assert (await client.get("/entry-control")).status_code == 200
            sentry_sdk.flush()
            assert any(e.get("message") == "ENTRY_ORDINARY_CONTROL" for e in collected.events)
            assert "ENTRY_ORDINARY_CONTROL" in caplog.text
    finally:
        sentry_sdk.get_client().close()


@pytest.mark.parametrize("path", [PATH, PATH + "/"])
async def test_alias_cors_and_late_send(path, checkout):
    h = checkout
    r = await h.client.options(
        path, headers={"origin": "https://app.biotact.uz", "access-control-request-method": "POST"}
    )
    assert r.headers["cache-control"] == "no-store"
    assert (await h.client.post(path, json={"token": TOKEN}, headers=h.headers)).status_code == 200
    collected = Captured()
    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=collected,
        integrations=[FastApiIntegration()],
        before_send=filter_referral_telemetry,
        before_send_transaction=filter_referral_telemetry,
    )

    async def broken(scope, receive, send):
        async def fail(_message):
            raise ConnectionError("late " + TOKEN)

        await h.app(scope, receive, fail)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=broken), base_url="http://test"
        ) as client:
            with pytest.raises(ConnectionError):
                await client.post(path, json={"token": TOKEN}, headers=h.headers)
        sentry_sdk.flush()
        assert not collected.events
    finally:
        sentry_sdk.get_client().close()
