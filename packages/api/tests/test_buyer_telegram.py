"""TG1 real migrated SQL/HTTP boundaries, concurrency and response-loss authority."""

import asyncio
import logging
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from src.buyer_auth.models import BuyerSession, BuyerTelegramRequest
from src.buyer_auth.security import CONTEXT_COOKIE, SESSION_COOKIE, BuyerAuthError
from src.buyer_auth.service import issue_session
from src.loyalty.models import CustomerProfile, PointLedger
from src.users.models import User
from tests.test_buyer_auth import PATH, buyer, context, scratch  # noqa: F401

BOT = "/api/v1/bot/buyer/telegram"


@pytest.fixture
async def tg(buyer):  # noqa: F811
    config = buyer.auth.config
    config.buyer_email_auth_enabled = False
    config.buyer_telegram_auth_enabled = True
    config.buyer_telegram_bot_username = "synthetic_bot"
    config.telegram_bot_token = "synthetic-bot-authority"
    config.loyalty_enabled = True
    buyer.telegram = buyer.app.state.buyer_telegram_auth
    buyer.sends = []

    async def send(actor, prompt):
        buyer.sends.append((actor, prompt))
        return "sent", 0

    buyer.telegram.sender.send = send
    return buyer


async def create(tg, key="a" * 32, client=None, headers=None):
    client = client or tg.client
    headers = headers or await context(client)
    response = await client.post(
        PATH + "/telegram/requests", json={"request_key": key}, headers=headers
    )
    assert response.status_code == 202, response.text
    return response.json(), headers


async def decide(tg, handle, actor=8801, decision="confirm"):
    return await tg.client.post(
        BOT + "/decision",
        json={"request_id": handle, "telegram_id": actor, "decision": decision},
        headers={"X-Bot-Token": tg.auth.config.telegram_bot_token},
    )


async def complete(tg, row, headers, client=None):
    return await (client or tg.client).post(
        PATH + "/telegram/complete", json={"request_id": row["request_id"]}, headers=headers
    )


async def test_full_identity_club_replay_rotation_and_no_business_writes(tg):
    attempt, headers = await create(tg)
    assert attempt["delivery"] == "not_requested"
    assert tg.sends == []
    assert attempt["bot_url"] == "https://t.me/synthetic_bot?start=web_" + attempt["request_id"]
    assert (await complete(tg, attempt, headers)).status_code == 409
    shown = await tg.client.post(
        BOT + "/present",
        json={"request_id": attempt["request_id"], "telegram_id": 8801},
        headers={"X-Bot-Token": tg.auth.config.telegram_bot_token},
    )
    assert shown.status_code == 200
    assert shown.json()["state"] == "pending"
    assert shown.json()["prompt"]["comparison"] == attempt["comparison"]
    assert (await decide(tg, attempt["request_id"])).json() == {"state": "approved"}
    assert (await decide(tg, attempt["request_id"], decision="decline")).status_code == 409
    result = await complete(tg, attempt, headers)
    assert result.status_code == 200, result.text
    token = tg.client.cookies.get(SESSION_COOKIE)
    assert (await complete(tg, attempt, headers)).status_code == 200
    assert tg.client.cookies.get(SESSION_COOKIE) == token
    assert (await tg.client.get(PATH + "/club")).json() == {
        "is_member": False,
        "tier": "newbie",
        "balance": 0,
        "available": 0,
        "reserved_until": None,
    }
    assert (await tg.client.get(PATH + "/club?user_id=1")).status_code == 422
    async with tg.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 1
        assert await db.scalar(select(func.count()).select_from(BuyerSession)) == 1
        assert await db.scalar(select(func.count()).select_from(CustomerProfile)) == 0
        assert await db.scalar(select(func.count()).select_from(PointLedger)) == 0
    rotation = await tg.client.post(
        PATH + "/session/rotate", json={}, headers={"X-CSRF-Token": result.json()["csrf_token"]}
    )
    assert rotation.status_code == 200
    assert (await complete(tg, attempt, headers)).status_code == 401


@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("late_after", [False, True])
async def test_held_committed_cookie_barrier_context_logout_fresh_context_and_both_cookie_orders(
    tg, recovery, late_after
):
    a, headers = await create(tg)
    browser = tg.client.cookies.get(CONTEXT_COOKIE)
    assert (await decide(tg, a["request_id"], 8801)).status_code == 200
    # Actual ASGI response is held at response.start BEFORE HTTPX sees Set-Cookie.
    committed, release = asyncio.Event(), asyncio.Event()
    original = tg.client._transport.app
    held = False

    async def hold(scope, receive, send):
        async def delayed(message):
            nonlocal held
            if (
                scope["path"].endswith("/complete")
                and message["type"] == "http.response.start"
                and message["status"] == 200
                and not held
            ):
                held = True
                committed.set()
                await release.wait()
            await send(message)

        await original(scope, receive, delayed)

    if recovery:
        initial = await complete(tg, a, headers)
        assert initial.status_code == 200
        tg.client.cookies.delete(SESSION_COOKIE)
    tg.client._transport.app = hold
    pending = asyncio.create_task(complete(tg, a, headers))
    await asyncio.wait_for(committed.wait(), 10)
    other = tg.another()
    other.cookies.set(CONTEXT_COOKIE, browser, domain="buyer.test", path="/")
    try:
        conflict = await other.post(
            PATH + "/telegram/requests", json={"request_key": "b" * 32}, headers=headers
        )
        assert conflict.status_code == 409
        assert conflict.json() == {"error": "already_authenticated"}
        assert "set-cookie" not in conflict.headers
        assert tg.sends == []
        async with tg.sessions() as db:
            assert await db.scalar(select(func.count()).select_from(BuyerTelegramRequest)) == 1
        # Unpresented other-context control session survives context-scoped logout.
        async with tg.sessions.begin() as db:
            user = await db.scalar(select(User).where(User.telegram_id == 8801))
            control, control_token = await issue_session(db, user, tg.clock())
            control_id = control.id
        cleared = await other.post(PATH + "/logout", json={}, headers=headers)
        assert cleared.status_code == 204
        # Replaying the old context cannot recover or start again, even after revocation.
        replay = tg.another()
        replay.cookies.set(CONTEXT_COOKIE, browser, domain="buyer.test", path="/")
        assert (await complete(tg, a, headers, replay)).status_code == 401
        assert (
            await replay.post(
                PATH + "/telegram/requests", json={"request_key": "c" * 32}, headers=headers
            )
        ).status_code == 409
        await replay.aclose()
        # Acknowledge cookie clearing before requesting the distinct C2.
        b, bheaders = await create(tg, "d" * 32, other)
        assert other.cookies.get(CONTEXT_COOKIE) != browser
        assert (await decide(tg, b["request_id"], 8802)).status_code == 200
        if not late_after:
            release.set()
            late = await pending
            other.cookies.set(
                SESSION_COOKIE, tg.client.cookies.get(SESSION_COOKIE), domain="buyer.test", path="/"
            )
            assert (await other.get(PATH + "/session")).status_code == 401
        done_b = await complete(tg, b, bheaders, other)
        assert done_b.status_code == 200
        btoken = other.cookies.get(SESSION_COOKIE)
        if late_after:
            release.set()
            late = await pending
            assert late.status_code == 200
            other.cookies.set(
                SESSION_COOKIE, tg.client.cookies.get(SESSION_COOKIE), domain="buyer.test", path="/"
            )
            assert (await other.get(PATH + "/session")).status_code == 401
            assert (await other.get(PATH + "/club")).status_code == 401
        other.cookies.set(SESSION_COOKIE, btoken, domain="buyer.test", path="/")
        assert (await other.get(PATH + "/session")).json()["user"]["telegram_id"] == 8802
        async with tg.sessions() as db:
            assert (await db.get(BuyerSession, control_id)).revoked_at is None
        other.cookies.set(SESSION_COOKIE, control_token, domain="buyer.test", path="/")
        assert (await other.get(PATH + "/session")).status_code == 200
    finally:
        release.set()
        await pending
        await other.aclose()


async def test_empty_context_concurrency_replacement_and_complete_winner(tg):
    headers = await context(tg.client)
    browser = tg.client.cookies.get(CONTEXT_COOKIE)
    # Independent sessions simultaneously cross the initially empty advisory boundary.
    outcomes = await asyncio.gather(
        *(tg.telegram.create(c * 32, browser, "race", None) for c in "abc")
    )
    async with tg.sessions() as db:
        rows = (await db.scalars(select(BuyerTelegramRequest))).all()
        assert sorted(r.state for r in rows) == ["cancelled", "cancelled", "pending"]
        winner = next(r for r in rows if r.state == "pending")
    for attempt in outcomes:
        if attempt["request_id"] != winner.id:
            assert (await decide(tg, attempt["request_id"])).json()["state"] == "cancelled"
            assert (await complete(tg, attempt, headers)).status_code == 401
    assert (await decide(tg, winner.id)).json()["state"] == "approved"
    done = await tg.telegram.complete(winner.id, browser, "race", None)
    assert done[0]["user"]["telegram_id"] == 8801
    with pytest.raises(BuyerAuthError) as refused:
        await tg.telegram.create("d" * 32, browser, "race", None)
    assert refused.value.status == 409


async def test_decision_race_wrong_actor_expiry_and_disabled_session_preservation(tg):
    attempt, headers = await create(tg)
    result = await asyncio.gather(
        decide(tg, attempt["request_id"], 8801), decide(tg, attempt["request_id"], 8802, "decline")
    )
    assert sorted(r.status_code for r in result) == [200, 401]
    tg.clock.advance(600)
    assert (await complete(tg, attempt, headers)).status_code == 401
    assert (
        await tg.client.post(
            PATH + "/telegram/status", json={"request_id": attempt["request_id"]}, headers=headers
        )
    ).json()["state"] in {"declined", "expired"}
    tg.auth.config.buyer_telegram_auth_enabled = False
    assert (
        await tg.client.post(
            BOT + "/decision", json={}, headers={"X-Bot-Token": tg.auth.config.telegram_bot_token}
        )
    ).status_code == 404
    assert (
        await tg.client.post(PATH + "/telegram/status", json={}, headers=headers)
    ).status_code == 404
    assert (await tg.client.post(PATH + "/context", json={})).status_code == 404
    assert (await tg.client.post(PATH + "/logout", json={}, headers=headers)).status_code == 204


async def test_expired_hint_direct_send_idempotency_and_wrong_actor(tg):
    async with tg.sessions.begin() as db:
        user = User(telegram_id=8801, is_active=True, is_admin=True, phone="synthetic")
        db.add(user)
        await db.flush()
        session, token = await issue_session(db, user, tg.clock())
        session.idle_expires_at = tg.clock() - timedelta(seconds=1)
    tg.client.cookies.set(SESSION_COOKIE, token, domain="buyer.test", path="/")
    attempt, headers = await create(tg)
    assert attempt["delivery"] == "sent"
    assert len(tg.sends) == 1
    assert tg.sends[0][1]["comparison"] == attempt["comparison"]
    assert (await create(tg, headers=headers))[0] == attempt
    assert len(tg.sends) == 1
    assert (await decide(tg, attempt["request_id"], 8802)).status_code == 401
    assert (await decide(tg, attempt["request_id"], 8801)).status_code == 200
    assert (await complete(tg, attempt, headers)).status_code == 200
    async with tg.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 1
        assert (await db.scalar(select(User))).is_admin is True


async def test_limits_malformed_and_privacy_at_real_middleware(tg, caplog):
    attempt, headers = await create(tg)
    canary = "PRIVATE_REQUEST_HANDLE_CANARY"
    with caplog.at_level(logging.DEBUG):
        wrong = await tg.client.post(
            BOT + "/decision",
            json={"request_id": canary, "telegram_id": 8801, "decision": "confirm"},
            headers={"X-Bot-Token": "wrong"},
        )
        assert wrong.status_code == 401
        for _ in range(100):
            response = await tg.client.post(
                PATH + "/telegram/status", json={"request_id": canary}, headers=headers
            )
            assert response.status_code == 401
        limited = await tg.client.post(
            PATH + "/telegram/status", json={"request_id": attempt["request_id"]}, headers=headers
        )
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) > 0
        # Poll budget cannot starve the completion budget.
        assert (await decide(tg, attempt["request_id"])).status_code == 200
        assert (await complete(tg, attempt, headers)).status_code == 200
        logging.getLogger("positive-control").info("unrelated-event-survives")
    assert canary not in caplog.text
    assert "unrelated-event-survives" in caplog.text
    for response in (wrong, limited):
        assert response.headers["cache-control"] == "no-store"
        assert not any(k.startswith("access-control-") for k in response.headers)


async def test_failed_commit_rolls_back_user_session_and_completion(tg, monkeypatch):
    attempt, headers = await create(tg)
    await decide(tg, attempt["request_id"])
    from src.buyer_auth import telegram as module

    original = module.issue_session

    async def fail(db, user, at, **kwargs):
        await original(db, user, at, **kwargs)
        await db.execute(text("SELECT 1/0"))

    monkeypatch.setattr(module, "issue_session", fail)
    assert (await complete(tg, attempt, headers)).status_code == 503
    async with tg.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 0
        assert await db.scalar(select(func.count()).select_from(BuyerSession)) == 0
        assert (await db.get(BuyerTelegramRequest, attempt["request_id"])).state == "approved"
