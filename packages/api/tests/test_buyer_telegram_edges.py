"""Controlled lock orders, bounded external I/O and retained authority edges."""

import asyncio
import logging
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import func, select

from src.buyer_auth.cleanup import cancel_telegram_batch, cleanup_batch
from src.buyer_auth.models import BuyerSession, BuyerTelegramRequest
from src.buyer_auth.security import BuyerAuthError
from src.buyer_auth.telegram_transport import TelegramSender
from src.users.models import User
from tests import test_buyer_telegram as telegram_tests
from tests.test_buyer_auth import buyer, scratch  # noqa: F401
from tests.test_buyer_telegram import complete, create, decide

tg = telegram_tests.tg


@pytest.mark.parametrize("winner", ["create", "complete", "logout"])
async def test_controlled_context_lock_orders(tg, monkeypatch, winner):
    attempt, headers = await create(tg)
    await decide(tg, attempt["request_id"])
    browser = tg.client.cookies.get("__Host-biotact_auth")
    entered, release = asyncio.Event(), asyncio.Event()
    original = tg.telegram.lock_context

    async def lock(db, digest):
        await original(db, digest)
        if asyncio.current_task().get_name() == "winner":
            entered.set()
            await release.wait()

    monkeypatch.setattr(tg.telegram, "lock_context", lock)

    async def operation(name):
        if name == "create":
            return await tg.telegram.create("b" * 32, browser, "controlled", None)
        if name == "complete":
            return await tg.telegram.complete(attempt["request_id"], browser, "controlled", None)
        return await tg.telegram.logout(None, browser, headers["X-CSRF-Token"])

    first = asyncio.create_task(operation(winner), name="winner")
    await entered.wait()
    loser = "create" if winner == "complete" else "complete"
    second = asyncio.create_task(operation(loser), name="second")
    release.set()
    outcomes = await asyncio.gather(first, second, return_exceptions=True)
    assert not isinstance(outcomes[0], Exception)
    assert isinstance(outcomes[1], BuyerAuthError)
    assert outcomes[1].status == (409 if winner == "complete" else 401)
    async with tg.sessions() as db:
        row = await db.get(BuyerTelegramRequest, attempt["request_id"])
        assert row.state == ("completed" if winner == "complete" else "cancelled")
        assert await db.scalar(select(func.count()).select_from(BuyerSession)) == (
            1 if winner == "complete" else 0
        )


async def test_completion_first_then_context_logout_revokes_and_other_context_survives(tg):
    attempt, headers = await create(tg)
    await decide(tg, attempt["request_id"])
    browser = tg.client.cookies.get("__Host-biotact_auth")
    await tg.telegram.complete(attempt["request_id"], browser, "peer", None)
    await tg.telegram.logout(None, browser, headers["X-CSRF-Token"])
    with pytest.raises(BuyerAuthError):
        await tg.telegram.complete(attempt["request_id"], browser, "peer", None)
    async with tg.sessions() as db:
        row = await db.scalar(select(BuyerSession))
        assert row.revoked_at is not None


async def test_inactive_customer_does_not_issue_or_modify_existing_authority(tg):
    async with tg.sessions.begin() as db:
        db.add(User(telegram_id=8801, is_active=False, is_admin=True, phone="synthetic-contact"))
    attempt, headers = await create(tg)
    await decide(tg, attempt["request_id"])
    assert (await complete(tg, attempt, headers)).status_code == 401
    async with tg.sessions() as db:
        user = await db.scalar(select(User))
        assert user.is_active is False
        assert user.is_admin is True
        assert user.phone == "synthetic-contact"
        assert await db.scalar(select(func.count()).select_from(BuyerSession)) == 0


async def test_sender_capacity_survives_cancellation_until_transport_finishes(tg):
    sender = TelegramSender(tg.auth.config)
    entered, release, scheduled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = 0

    async def transport(actor, prompt):
        nonlocal calls
        calls += 1
        if calls == 4:
            entered.set()
        await release.wait()
        return "sent", 0

    sender._send = transport
    tasks = [asyncio.create_task(sender.send(i + 1, {})) for i in range(4)]
    await entered.wait()
    tasks[0].cancel()
    asyncio.get_running_loop().call_soon(scheduled.set)
    await scheduled.wait()
    assert await sender.send(9, {}) == ("unavailable", 0)
    assert calls == 4
    release.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert isinstance(results[0], asyncio.CancelledError)
    assert await sender.send(10, {}) == ("sent", 0)


@pytest.mark.parametrize("outcome", ["sent", "unavailable", "timeout", "throttle"])
async def test_actual_http_transport_tls_timeout_payload_and_private_diagnostics(
    tg, monkeypatch, caplog, outcome
):
    original = httpx.AsyncClient
    seen = []

    def transport(request):
        seen.append(request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("synthetic-private-transport-canary")
        if outcome == "throttle":
            return httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 120}})
        return httpx.Response(200 if outcome == "sent" else 403, json={"ok": outcome == "sent"})

    def client(**kwargs):
        assert kwargs == {
            "timeout": 10,
            "verify": True,
            "follow_redirects": False,
            "trust_env": False,
        }
        return original(transport=httpx.MockTransport(transport))

    monkeypatch.setattr(httpx, "AsyncClient", client)
    prompt = {
        "site": "https://buyer.test",
        "created_at": "2030-01-01T00:00:00Z",
        "expires_at": "2030-01-01T00:10:00Z",
        "comparison": "123456",
        "confirm_data": "web:" + "a" * 32 + ":confirm",
        "decline_data": "web:" + "a" * 32 + ":decline",
    }
    with caplog.at_level(logging.DEBUG):
        result = await TelegramSender(tg.auth.config).send(8801, prompt)
        logging.getLogger("positive-control").info("transport-control-survives")
    assert (
        result
        == {
            "sent": ("sent", 0),
            "unavailable": ("unavailable", 0),
            "timeout": ("uncertain", 0),
            "throttle": ("unavailable", 120),
        }[outcome]
    )
    assert len(seen) == 1
    assert seen[0].url.host == "api.telegram.org"
    assert b'"comparison"' not in seen[0].content
    assert b"123456" in seen[0].content
    assert tg.auth.config.telegram_bot_token not in caplog.text
    assert "synthetic-private-transport-canary" not in caplog.text
    assert "transport-control-survives" in caplog.text


async def test_bounded_cancellation_retention_and_completed_session_cleanup(tg):
    attempt, headers = await create(tg)
    assert await cancel_telegram_batch(tg.sessions, tg.clock()) == 1
    assert (await complete(tg, attempt, headers)).status_code == 401
    await cleanup_batch(tg.sessions, tg.clock() + timedelta(hours=23))
    async with tg.sessions() as db:
        assert await db.get(BuyerTelegramRequest, attempt["request_id"]) is not None
    removed = await cleanup_batch(tg.sessions, tg.clock() + timedelta(days=2))
    assert removed >= 1
    async with tg.sessions() as db:
        assert await db.get(BuyerTelegramRequest, attempt["request_id"]) is None


@pytest.mark.parametrize(("retry", "delay"), [(120, 0), (90, 0), (45, 50)])
async def test_direct_telegram_retry_after_persists_without_automatic_resend(tg, retry, delay):
    from src.buyer_auth.service import issue_session
    from src.buyer_auth.telegram import TelegramAuthService

    async with tg.sessions.begin() as db:
        user = User(telegram_id=8801, is_active=True, is_admin=False)
        db.add(user)
        await db.flush()
        session, token = await issue_session(db, user, tg.clock())
        session.idle_expires_at = tg.clock() - timedelta(seconds=1)
    tg.client.cookies.set("__Host-biotact_buyer", token, domain="buyer.test", path="/")
    sends = []

    async def throttled(actor, prompt):
        sends.append(actor)
        tg.clock.advance(delay)
        return "unavailable", retry

    tg.telegram.sender.send = throttled
    attempt, _headers = await create(tg)
    assert attempt["delivery"] == "unavailable"
    # A fresh service object shares only SQL state, not the sending worker's memory.
    worker = TelegramAuthService(type(tg.auth)(tg.auth.config, tg.sessions, clock=tg.clock))

    async def delivered(actor, prompt):
        sends.append(actor)
        return "sent", 0

    worker.sender.send = delivered
    tg.app.state.buyer_telegram_auth = worker
    tg.clock.advance(retry - 1)
    response = await tg.client.post(
        "/api/v1/bot/buyer/telegram/present",
        json={"request_id": attempt["request_id"], "telegram_id": 8801},
        headers={"X-Bot-Token": tg.auth.config.telegram_bot_token},
    )
    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    assert sends == [8801]
    tg.clock.advance(1)
    deliberate, _ = await create(tg, "b" * 32)
    assert deliberate["delivery"] == "sent"
    assert sends == [8801, 8801]


async def test_cancelled_direct_owner_retains_observed_retry_after(tg):
    from src.buyer_auth.service import issue_session
    from src.buyer_auth.telegram import TelegramAuthService
    from tests.test_buyer_auth import context

    async with tg.sessions.begin() as db:
        user = User(telegram_id=8801, is_active=True, is_admin=False)
        db.add(user)
        await db.flush()
        session, token = await issue_session(db, user, tg.clock())
        session.idle_expires_at = tg.clock() - timedelta(seconds=1)
    await context(tg.client)
    browser = tg.client.cookies.get("__Host-biotact_auth")
    entered, release = asyncio.Event(), asyncio.Event()
    sender = TelegramSender(tg.auth.config)

    async def transport(actor, prompt):
        entered.set()
        await release.wait()
        return "unavailable", 90

    sender._send = transport
    tg.telegram.sender = sender
    owner = asyncio.create_task(tg.telegram.create("a" * 32, browser, "peer", token))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        owner.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await owner
    async with tg.sessions() as db:
        attempt = await db.scalar(select(BuyerTelegramRequest))
        assert attempt.delivery == "unavailable"
        handle = attempt.id
    worker = TelegramAuthService(type(tg.auth)(tg.auth.config, tg.sessions, clock=tg.clock))
    tg.clock.advance(89)
    with pytest.raises(BuyerAuthError) as caught:
        await worker.bridge(handle, 8801)
    assert caught.value.status == 429
    assert caught.value.retry == 1
    tg.clock.advance(1)
    assert (await worker.bridge(handle, 8801))["prompt"] is not None


async def test_retry_after_bridge_has_strict_private_guards_and_fails_closed(tg, monkeypatch):
    from src.buyer_auth.models import BuyerAuthLimit

    path = "/api/v1/bot/buyer/telegram/retry-after"
    payload = {"telegram_id": 8801, "retry_after": 90}
    headers = {"X-Bot-Token": tg.auth.config.telegram_bot_token}
    for credentials in ({}, {"X-Bot-Token": "wrong"}, {"Authorization": "Bearer synthetic"}):
        response = await tg.client.post(path, json=payload, headers=credentials)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
    async with tg.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(BuyerAuthLimit)) == 0
    for bad in (True, 0, -1, 1.5, "90", None):
        response = await tg.client.post(path, json={**payload, "retry_after": bad}, headers=headers)
        assert response.status_code == 422
    for bad in (True, 0, -1, 9223372036854775808, "8801"):
        response = await tg.client.post(path, json={**payload, "telegram_id": bad}, headers=headers)
        assert response.status_code == 422
    assert (
        await tg.client.post(path, json={**payload, "request_id": "a" * 32}, headers=headers)
    ).status_code == 422
    assert (
        await tg.client.post(
            path, content=b"x" * 4097, headers={**headers, "Content-Type": "application/json"}
        )
    ).status_code == 413
    assert (await tg.client.post(path, content="text", headers=headers)).status_code == 415
    tg.auth.config.buyer_telegram_auth_enabled = False
    assert (await tg.client.post(path, json=payload, headers=headers)).status_code == 404
    tg.auth.config.buyer_telegram_auth_enabled = True

    async def unavailable(*args):
        raise RuntimeError("private-storage-canary")

    monkeypatch.setattr(tg.telegram, "defer_prompt", unavailable)
    failed = await tg.client.post(path, json=payload, headers=headers)
    assert failed.status_code == 503
    assert failed.headers["cache-control"] == "no-store"
    assert "private-storage-canary" not in failed.text


async def test_retry_after_bridge_preserves_stronger_limits_and_only_changes_cooldown(tg):
    from src.buyer_auth.models import BuyerAuthLimit
    from src.buyer_auth.security import keyed
    from src.buyer_auth.telegram import TelegramAuthService

    attempt, _ = await create(tg)
    headers = {"X-Bot-Token": tg.auth.config.telegram_bot_token}
    payload = {"request_id": attempt["request_id"], "telegram_id": 8801}
    present = "/api/v1/bot/buyer/telegram/present"
    assert (await tg.client.post(present, json=payload, headers=headers)).status_code == 200
    cooldown = keyed(tg.auth.config, "rate-telegram-prompt-cooldown", "8801")
    async with tg.sessions() as db:
        before = {
            row.key: (row.events, row.expires_at)
            for row in (await db.scalars(select(BuyerAuthLimit))).all()
            if row.key != cooldown
        }
    # Two independent SQL transactions must never shorten each other's restriction.
    responses = await asyncio.gather(
        *[
            tg.client.post(
                "/api/v1/bot/buyer/telegram/retry-after",
                json={"telegram_id": 8801, "retry_after": seconds},
                headers=headers,
            )
            for seconds in (120, 20)
        ]
    )
    assert all(response.status_code == 200 and response.json() == {} for response in responses)
    async with tg.sessions() as db:
        after = {
            row.key: (row.events, row.expires_at)
            for row in (await db.scalars(select(BuyerAuthLimit))).all()
            if row.key != cooldown
        }
        assert after == before
        row = await db.get(BuyerTelegramRequest, attempt["request_id"])
        assert row.state == "pending"
        assert row.deciding_actor is None
        assert row.expected_actor is None
        assert row.delivery == "not_requested"
        assert await db.scalar(select(func.count()).select_from(User)) == 0
        assert await db.scalar(select(func.count()).select_from(BuyerSession)) == 0
    tg.app.state.buyer_telegram_auth = TelegramAuthService(
        type(tg.auth)(tg.auth.config, tg.sessions, clock=tg.clock)
    )
    tg.clock.advance(119)
    await cleanup_batch(tg.sessions, tg.clock())
    refused = await tg.client.post(present, json=payload, headers=headers)
    assert refused.status_code == 429
    assert refused.headers["retry-after"] == "1"
    other = await tg.client.post(present, json={**payload, "telegram_id": 8802}, headers=headers)
    assert other.status_code == 200
    assert other.json()["prompt"] is not None
    tg.clock.advance(1)
    assert (await tg.client.post(present, json=payload, headers=headers)).json()[
        "prompt"
    ] is not None


async def test_malformed_bridge_id_type_still_accounts_the_authenticated_actor(tg):
    from src.buyer_auth.models import BuyerAuthLimit
    from src.buyer_auth.security import keyed

    response = await tg.client.post(
        "/api/v1/bot/buyer/telegram/decision",
        json={"request_id": 123, "telegram_id": 8801, "decision": "confirm"},
        headers={"X-Bot-Token": tg.auth.config.telegram_bot_token},
    )
    assert response.status_code == 422
    async with tg.sessions() as db:
        row = await db.get(BuyerAuthLimit, keyed(tg.auth.config, "rate-telegram-actor", "8801"))
        assert row is not None
        assert len(row.events) == 1


async def test_context_capability_matrix_preserves_s3_fields_and_disabled_guards(tg):
    for sessions, email, telegram, expected in (
        (True, True, False, 200),
        (True, True, True, 200),
        (True, False, True, 200),
        (True, False, False, 404),
        (False, True, False, 404),
    ):
        tg.auth.config.buyer_sessions_enabled = sessions
        tg.auth.config.buyer_email_auth_enabled = email
        tg.auth.config.buyer_telegram_auth_enabled = telegram
        response = await tg.client.post("/api/v1/buyer/auth/context", json={})
        assert response.status_code == expected
        assert response.headers["cache-control"] == "no-store"
        if expected == 200:
            data = response.json()
            assert data["telegram_enabled"] is telegram
            assert len(data["csrf_token"]) == 64
            assert data["expires_at"].endswith("Z") or data["expires_at"].endswith("+00:00")
