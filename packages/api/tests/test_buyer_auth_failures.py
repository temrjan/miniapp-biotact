"""S3 failure accounting, cancellation, actual SMTP/SQL/SDK boundaries and retention."""

import asyncio
import json
import logging
import threading
from datetime import timedelta

import pytest
import sentry_sdk
from httpx import ASGITransport, AsyncClient
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError

from src.auth.privacy import filter_auth_telemetry
from src.buyer_auth.cleanup import cleanup_batch
from src.buyer_auth.mail import BuyerMailer
from src.buyer_auth.models import (
    BuyerAuthLimit,
    BuyerEmailChallenge,
    BuyerEmailIdentity,
    BuyerSession,
)
from src.buyer_auth.security import CONTEXT_COOKIE, SESSION_COOKIE, BuyerAuthError, keyed
from src.buyer_auth.service import BuyerAuthService
from src.users.models import User
from tests.test_buyer_auth import ORIGIN, PATH, challenge, context, signed_in, verify
from tests.test_buyer_auth import buyer as buyer
from tests.test_buyer_auth import scratch as scratch
from tests.test_employee_referral_k1_receiver import Captured


async def test_committed_limits_survive_errors_and_second_instance(buyer):
    headers = await context(buyer.client)
    second = BuyerAuthService(buyer.auth.config, buyer.sessions, buyer.auth.mailer, buyer.clock)
    for index in range(100):
        auth = buyer.auth if index % 2 else second
        with pytest.raises(BuyerAuthError, match="invalid_authentication"):
            await auth.verify(
                "malformed", "bad", buyer.client.cookies[CONTEXT_COOKIE], "same-peer", None
            )
    with pytest.raises(BuyerAuthError, match="try_later") as failure:
        await second.verify(
            "malformed", "bad", buyer.client.cookies[CONTEXT_COOKIE], "same-peer", None
        )
    assert failure.value.retry == 600
    async with buyer.sessions() as db:
        row = await db.get(
            BuyerAuthLimit, keyed(buyer.auth.config, "rate-verify-peer", "same-peer")
        )
        assert len(row.events) == 100
        assert "same-peer" not in row.key
    buyer.clock.advance(600)
    with pytest.raises(BuyerAuthError, match="invalid_authentication"):
        await second.verify(
            "malformed", "bad", buyer.client.cookies[CONTEXT_COOKIE], "same-peer", None
        )
    # HTTP failure uses its own persisted peer count, observed through a fresh connection.
    response = await buyer.client.post(
        PATH + "/email/verify", json={"challenge_id": "bad", "code": "bad"}, headers=headers
    )
    assert response.status_code == 401
    async with buyer.sessions() as db:
        assert sum(len(row.events) for row in await db.scalars(select(BuyerAuthLimit))) == 3


async def test_context_budget_and_untrusted_forwarded_peer(buyer):
    for _ in range(30):
        buyer.client.cookies.clear()
        await context(buyer.client)
    buyer.client.cookies.clear()
    response = await buyer.client.post(
        PATH + "/context",
        json={},
        headers={"X-Real-IP": "192.0.2.99", "X-Forwarded-For": "192.0.2.100"},
    )
    assert response.status_code == 429
    assert response.headers["retry-after"] == "600"
    # Restart-equivalent service reads the same durable bucket.
    second = BuyerAuthService(buyer.auth.config, buyer.sessions, buyer.auth.mailer, buyer.clock)
    with pytest.raises(BuyerAuthError, match="try_later"):
        await second.account("context-peer", "127.0.0.1", 30, 600)


async def test_email_hour_and_peer_hour_limits(buyer):
    headers = await context(buyer.client)
    for index in range(5):
        if index:
            buyer.clock.advance(60)
        await challenge(buyer, headers=headers)
    buyer.clock.advance(60)
    response = await buyer.client.post(
        PATH + "/email/challenges", json={"email": "BUYER@EXAMPLE.TEST"}, headers=headers
    )
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) == 3300
    assert len(buyer.messages) == 5
    # Distinct addresses share the peer's 30/hour quota.
    for index in range(25):
        await challenge(buyer, email=f"separate{index}@example.test", headers=headers)
    response = await buyer.client.post(
        PATH + "/email/challenges", json={"email": "overflow@example.test"}, headers=headers
    )
    assert response.status_code == 429
    assert len(buyer.messages) == 30


async def test_storage_failure_before_send_and_atomic_identity_rollback(buyer, monkeypatch):
    proof = await challenge(buyer)
    engine = buyer.sessions.kw["bind"]
    canary = "PRIVATE_SQL_CANARY"

    def fail_session(conn, cursor, statement, parameters, execution_context, executemany):
        if statement.startswith("INSERT INTO buyer_sessions"):
            raise IntegrityError("private statement", {"canary": canary}, RuntimeError(canary))

    event.listen(engine.sync_engine, "before_cursor_execute", fail_session)
    try:
        response = await verify(buyer.client, proof)
        assert response.status_code == 503
        assert response.json() == {"error": "authentication_unavailable"}
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", fail_session)
    async with buyer.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 0
        assert await db.scalar(select(func.count()).select_from(BuyerEmailIdentity)) == 0
        assert await db.scalar(select(func.count()).select_from(BuyerSession)) == 0
        assert (await db.get(BuyerEmailChallenge, proof[0])).state == "ready"
        assert (await db.get(BuyerEmailChallenge, proof[0])).attempts == 0
        assert sum(len(row.events) for row in await db.scalars(select(BuyerAuthLimit))) == 5
    assert (await verify(buyer.client, proof)).status_code == 200
    buyer.client.cookies.clear()
    headers = await context(buyer.client)
    before = len(buyer.messages)

    async def no_account(*args, **kwargs):
        raise RuntimeError(canary)

    monkeypatch.setattr(buyer.auth, "_limits", no_account)
    response = await buyer.client.post(
        PATH + "/email/challenges", json={"email": "new@example.test"}, headers=headers
    )
    assert response.status_code == 503
    assert len(buyer.messages) == before


@pytest.mark.parametrize("failure", [RuntimeError("smtp-canary"), TimeoutError("smtp-timeout")])
async def test_mail_failure_keeps_proof_unusable_and_quota_committed(buyer, monkeypatch, failure):
    headers = await context(buyer.client)

    def fail(*args):
        raise failure

    monkeypatch.setattr(buyer.auth.mailer, "_send", fail)
    response = await buyer.client.post(
        PATH + "/email/challenges", json={"email": "a@example.test"}, headers=headers
    )
    assert response.status_code == 503
    async with buyer.sessions() as db:
        row = (await db.scalars(select(BuyerEmailChallenge))).one()
        assert row.state == "sending"
        assert sum(len(row.events) for row in await db.scalars(select(BuyerAuthLimit))) == 4
    response = await buyer.client.post(
        PATH + "/email/challenges", json={"email": "a@example.test"}, headers=headers
    )
    assert response.status_code == 429


async def test_delayed_send_superseded_and_no_sql_lock_during_smtp(buyer, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    captured = []

    async def delayed(recipient, code):
        captured.append(code)
        if len(captured) == 1:
            entered.set()
            await release.wait()

    monkeypatch.setattr(buyer.auth.mailer, "send", delayed)
    headers = await context(buyer.client)
    first = asyncio.create_task(
        buyer.client.post(
            PATH + "/email/challenges", json={"email": "buyer@example.test"}, headers=headers
        )
    )
    await asyncio.wait_for(entered.wait(), 5)
    try:
        # This exact reservation owner can be acquired from a different connection during I/O.
        async with buyer.sessions.begin() as db:
            await db.execute(text("SET LOCAL lock_timeout='1s'"))
            await buyer.auth._lock_email(db, "buyer@example.test")
        buyer.clock.advance(60)
        second = await buyer.client.post(
            PATH + "/email/challenges", json={"email": "buyer@example.test"}, headers=headers
        )
        assert second.status_code == 202
    finally:
        release.set()
    assert (await first).status_code == 503
    async with buyer.sessions() as db:
        assert sorted(row.state for row in await db.scalars(select(BuyerEmailChallenge))) == [
            "ready",
            "superseded",
        ]
    assert (
        await verify(buyer.client, (second.json()["challenge_id"], captured[1], headers))
    ).status_code == 200


async def test_smtp_capacity_cancel_and_unrelated_request_progress(buyer, monkeypatch):
    entered = [threading.Event() for _ in range(4)]
    release = threading.Event()
    lock = threading.Lock()
    next_index = 0

    def blocked(recipient, code):
        nonlocal next_index
        with lock:
            index = next_index
            next_index += 1
        entered[index].set()
        assert release.wait(10), "Synthetic transport barrier was not released"

    monkeypatch.setattr(buyer.auth.mailer, "_send", blocked)
    tasks = [
        asyncio.create_task(buyer.auth.mailer.send("a@example.test", "000007")) for _ in range(4)
    ]
    try:
        for signal in entered:
            assert await asyncio.to_thread(signal.wait, 5)
        tasks[0].cancel()
        tasks[0].cancel()
        with pytest.raises(BuyerAuthError, match="authentication_unavailable"):
            await BuyerMailer(buyer.auth.config).send("overflow@example.test", "000007")
        assert buyer.auth.mailer.in_flight == 4
        assert not tasks[0].done()
        response = await asyncio.wait_for(buyer.client.get("/api/v1/buyer/auth/unknown"), 1)
        assert response.status_code == 404
    finally:
        release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
    assert isinstance(results[0], asyncio.CancelledError)
    assert buyer.auth.mailer.in_flight == 0
    assert next_index == 4


async def test_cancelled_reservation_never_becomes_ready(buyer, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()

    async def paused(recipient, code):
        entered.set()
        await release.wait()

    monkeypatch.setattr(buyer.auth.mailer, "send", paused)
    headers = await context(buyer.client)
    task = asyncio.create_task(
        buyer.client.post(
            PATH + "/email/challenges", json={"email": "cancel@example.test"}, headers=headers
        )
    )
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with buyer.sessions() as db:
        assert (await db.scalars(select(BuyerEmailChallenge))).one().state == "sending"


async def test_parallel_rotation_single_winner_and_logout_other_browser(buyer):
    response, _proof = await signed_in(buyer)
    token = buyer.client.cookies[SESSION_COOKIE]
    csrf_token = response.json()["csrf_token"]

    async def rotate():
        try:
            return await buyer.auth.use_session(token, rotate=True, supplied_csrf=csrf_token)
        except BuyerAuthError as error:
            return error.status

    results = await asyncio.gather(rotate(), rotate())
    assert sum(isinstance(item, tuple) for item in results) == 1
    assert 401 in results
    buyer.clock.advance(60)
    async with buyer.another() as other:
        other_response, _proof = await signed_in(buyer, other)
        winner = next(item for item in results if isinstance(item, tuple))
        await buyer.auth.logout(winner[1], winner[0]["csrf_token"])
        assert (await other.get(PATH + "/session")).json()["user"] == other_response.json()["user"]
    with pytest.raises(BuyerAuthError, match="invalid_authentication"):
        await buyer.auth.use_session(winner[1])


async def test_bounded_cleanup_preserves_active_identity_and_effective_limits(buyer):
    await signed_in(buyer)
    at = buyer.clock()
    async with buyer.sessions.begin() as db:
        for index in range(510):
            db.add(
                BuyerAuthLimit(
                    key=f"expired-{index}", events=[], expires_at=at - timedelta(seconds=1)
                )
            )
        db.add(
            BuyerAuthLimit(
                key="effective", events=[at.timestamp()], expires_at=at + timedelta(hours=1)
            )
        )
        session = (await db.scalars(select(BuyerSession))).one()
        db.add(
            BuyerSession(
                id="expired-session",
                user_id=session.user_id,
                verifier_digest="expired-digest",
                generation=1,
                created_at=at - timedelta(days=100),
                authenticated_at=at - timedelta(days=100),
                last_used_at=at - timedelta(days=100),
                absolute_expires_at=at - timedelta(days=10),
                idle_expires_at=at - timedelta(days=10),
            )
        )
        proof = (await db.scalars(select(BuyerEmailChallenge))).one()
        db.add(
            BuyerEmailChallenge(
                id="old-proof",
                email=proof.email,
                browser_digest="digest",
                purpose="sign_in",
                code_digest="digest",
                state="sending",
                attempts=0,
                created_at=at - timedelta(days=2),
                expires_at=at - timedelta(days=2),
            )
        )
    assert await cleanup_batch(buyer.sessions, at) == 500
    assert await cleanup_batch(buyer.sessions, at) == 12
    assert await cleanup_batch(buyer.sessions, at) == 0
    assert (await buyer.client.get(PATH + "/session")).status_code == 200
    async with buyer.sessions() as db:
        assert await db.get(BuyerAuthLimit, "effective") is not None
        assert await db.scalar(select(func.count()).select_from(BuyerEmailIdentity)) == 1
        assert await db.scalar(select(func.count()).select_from(User)) == 1
        assert await db.scalar(select(func.count()).select_from(BuyerEmailChallenge)) == 1


async def test_actual_logs_sdk_late_breadcrumbs_and_positive_control(buyer, caplog):
    capture = Captured()
    canaries = [
        "email-canary@example.test",
        "BODY_CANARY",
        "SMTP_CANARY",
        buyer.auth.config.smtp_password.get_secret_value(),
    ]
    caplog.set_level(logging.DEBUG)
    with sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=capture,
        integrations=[FastApiIntegration(), SqlalchemyIntegration()],
        traces_sample_rate=1.0,
        before_send=filter_auth_telemetry,
        before_send_transaction=filter_auth_telemetry,
    ):

        @buyer.app.get(PATH + "/private-failure")
        async def private_failure():
            logging.getLogger("sqlalchemy.engine").error("%s", " ".join(canaries))
            sentry_sdk.capture_exception(RuntimeError(" ".join(canaries)))
            raise RuntimeError(" ".join(canaries))

        @buyer.app.get("/public-privacy-control")
        async def public_control():
            logging.getLogger("s3-positive").error("PUBLIC_S3_CONTROL")
            sentry_sdk.capture_message("PUBLIC_S3_CONTROL")
            return {"ok": True}

        async with AsyncClient(
            transport=ASGITransport(app=buyer.app, raise_app_exceptions=False),
            base_url=ORIGIN,
            headers={"Origin": ORIGIN},
        ) as client:
            response = await client.get(PATH + "/private-failure")
            assert response.status_code == 503
            headers = await context(client)
            proof = await challenge(buyer, client, canaries[0], headers)
            response = await verify(client, proof)
            assert response.status_code == 200
            canaries.extend(
                [
                    proof[1],
                    client.cookies[CONTEXT_COOKIE],
                    client.cookies[SESSION_COOKIE],
                    response.json()["csrf_token"],
                    headers["X-CSRF-Token"],
                ]
            )
            response = await client.post(
                PATH + "/email/verify", json={"code": canaries[1]}, headers=headers
            )
            assert response.status_code == 422
            logging.getLogger("uvicorn.access").info(
                '%s - "%s %s HTTP/%s" %s', "peer", "GET", PATH + "/" + canaries[1], "1.1", 200
            )
            sentry_sdk.add_breadcrumb(
                category="http", data={"url": ORIGIN + PATH + "/session", "body": canaries[1]}
            )
            response = await client.get("/public-privacy-control")
            assert response.json() == {"ok": True}
        sentry_sdk.flush()
    serialized = json.dumps(capture.events)
    assert "PUBLIC_S3_CONTROL" in serialized
    assert "PUBLIC_S3_CONTROL" in caplog.text
    for value in canaries:
        assert value not in caplog.text
        assert value not in serialized


async def test_mapped_inactive_send_path_and_reusable_issuer_rollback(buyer):
    from src.buyer_auth.service import issue_session

    at = buyer.clock()
    async with buyer.sessions.begin() as db:
        user = User(
            telegram_id=990701,
            first_name="Historic",
            is_active=False,
            is_admin=True,
            phone="synthetic-contact",
        )
        db.add(user)
        await db.flush()
        uid = user.id
        db.add(BuyerEmailIdentity(email="inactive@example.test", user_id=uid, verified_at=at))
    proof = await challenge(buyer, email="inactive@example.test")
    assert buyer.messages[-1][0] == "inactive@example.test"
    assert (await verify(buyer.client, proof)).status_code == 401
    async with buyer.sessions.begin() as db:
        user = await db.get(User, uid)
        assert user.telegram_id == 990701
        assert user.is_admin
        assert user.phone == "synthetic-contact"
        user.is_active = True
    buyer.clock.advance(60)
    response = await verify(buyer.client, await challenge(buyer, email="INACTIVE@EXAMPLE.TEST"))
    assert response.status_code == 200
    assert response.json()["user"] == {"id": uid, "telegram_id": 990701}
    token = buyer.client.cookies[SESSION_COOKIE]
    csrf_token = response.json()["csrf_token"]
    async with buyer.sessions.begin() as db:
        user = await db.get(User, uid)
        user.is_active = False
    # Logout must revoke even an inactive user's otherwise unexpired bearer.
    await buyer.auth.logout(token, csrf_token)
    async with buyer.sessions.begin() as db:
        user = await db.get(User, uid)
        user.is_active = True
    with pytest.raises(BuyerAuthError, match="invalid_authentication"):
        await buyer.auth.use_session(token)
    async with buyer.sessions() as db:
        user = await db.get(User, uid)
        await issue_session(db, user, at)
        await db.rollback()
    async with buyer.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(BuyerSession)) == 1


async def test_readiness_commit_failure_cannot_authorize(buyer, monkeypatch):
    engine = buyer.sessions.kw["bind"]
    captured = []
    original = buyer.auth.mailer.send

    async def capture(recipient, code):
        captured.append(code)
        await original(recipient, code)

    monkeypatch.setattr(buyer.auth.mailer, "send", capture)

    def fail_ready(conn, cursor, statement, parameters, execution_context, executemany):
        if (
            statement.startswith("UPDATE buyer_email_challenges SET state=")
            and "RETURNING buyer_email_challenges.id" in statement
        ):
            raise RuntimeError("SYNTHETIC_READINESS_WRITE_FAILURE")

    event.listen(engine.sync_engine, "before_cursor_execute", fail_ready)
    headers = await context(buyer.client)
    try:
        response = await buyer.client.post(
            PATH + "/email/challenges", json={"email": "ready@example.test"}, headers=headers
        )
        assert response.status_code == 503
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", fail_ready)
    assert len(captured) == 1
    async with buyer.sessions() as db:
        row = (await db.scalars(select(BuyerEmailChallenge))).one()
        assert row.state == "sending"
        handle = row.id
    assert (await verify(buyer.client, (handle, captured[0], headers))).status_code == 401


def test_late_cookie_dictionary_and_public_event_preservation():
    result = filter_auth_telemetry(
        {
            "message": "PUBLIC_CONTROL",
            "request": {
                "url": "https://buyer.test/public",
                "cookies": {SESSION_COOKIE: "SECRET_SESSION"},
            },
            "extra": {"csrf_token": "SECRET_CSRF"},
        },
        {},
    )
    assert result["message"] == "PUBLIC_CONTROL"
    assert "SECRET_SESSION" not in str(result)
    assert "SECRET_CSRF" not in str(result)


async def test_resend_verify_has_serialized_outcome(buyer):
    proof = await challenge(buyer)
    browser = buyer.client.cookies[CONTEXT_COOKIE]
    buyer.clock.advance(60)
    gate = asyncio.Event()

    async def consume():
        await gate.wait()
        try:
            return await buyer.auth.verify(proof[0], proof[1], browser, "race-peer", None)
        except BuyerAuthError as error:
            return error.status

    async def resend():
        await gate.wait()
        return await buyer.auth.reserve("buyer@example.test", browser, "race-peer", None)

    tasks = [asyncio.create_task(consume()), asyncio.create_task(resend())]
    gate.set()
    result, replacement = await asyncio.gather(*tasks)
    async with buyer.sessions() as db:
        original = await db.get(BuyerEmailChallenge, proof[0])
        new = await db.get(BuyerEmailChallenge, replacement["challenge_id"])
        assert new.state == "ready"
        assert (original.state, isinstance(result, tuple)) in {
            ("consumed", True),
            ("superseded", False),
        }
        if not isinstance(result, tuple):
            assert result == 401
