"""S3 browser contract on independently committed, actually migrated PostgreSQL."""

import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib import import_module

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.buyer_auth.models import (
    BuyerAuthLimit,
    BuyerEmailChallenge,
    BuyerEmailIdentity,
    BuyerSession,
)
from src.buyer_auth.security import CONTEXT_COOKIE, SESSION_COOKIE, BuyerAuthError, normalize_email
from src.buyer_auth.service import BuyerAuthService
from src.config import Settings
from src.loyalty.models import CustomerProfile, PointLedger
from src.users.models import User
from tests import test_category_schema_migration as migration

scratch = migration.scratch
PATH = "/api/v1/buyer/auth"
ORIGIN = "https://buyer.test"


def configuration(**changes):
    values = {
        "_env_file": None,
        "database_url": "postgresql+asyncpg://synthetic:synthetic@127.0.0.1/synthetic",
        "secret_key": SecretStr("synthetic-admin-only"),
        "buyer_sessions_enabled": True,
        "buyer_email_auth_enabled": True,
        "buyer_protection_key": SecretStr("synthetic-buyer-only-key-at-least-32-bytes"),
        "buyer_public_origin": ORIGIN,
        "smtp_user": "sender@example.test",
        "smtp_from_email": "sender@example.test",
        "smtp_password": SecretStr("synthetic-current-mail-password"),
        "smtp_from_name": "Biotact",
    }
    values.update(changes)
    return Settings(**values)


@dataclass
class Clock:
    at: datetime = field(default_factory=lambda: datetime(2030, 1, 1, tzinfo=UTC))

    def __call__(self):
        return self.at

    def advance(self, seconds):
        self.at += timedelta(seconds=seconds)


@dataclass
class BrowserFixture:
    db: migration.ScratchDatabase
    sessions: async_sessionmaker
    app: object
    client: AsyncClient
    auth: BuyerAuthService
    clock: Clock
    messages: list

    def another(self):
        return AsyncClient(
            transport=ASGITransport(app=self.app), base_url=ORIGIN, headers={"Origin": ORIGIN}
        )


@pytest_asyncio.fixture
async def buyer(scratch, monkeypatch):
    await asyncio.to_thread(scratch.migrate, target="head")
    engine = create_async_engine(scratch.url, pool_size=10)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    config = configuration(database_url=scratch.url)
    main = import_module("src.main")
    monkeypatch.setattr(main, "settings", config)
    monkeypatch.setattr(main, "async_session_maker", sessions)
    app = main.create_app()
    clock = Clock()
    auth = app.state.buyer_auth
    auth.clock = clock
    messages = []

    class SMTP:
        def __init__(self, host, port, *, timeout):
            assert (host, port, timeout) == ("smtp.gmail.com", 587, 10)
            self.tls = False
            self.logged_in = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def set_debuglevel(self, value):
            assert value == 0

        def ehlo(self):
            pass

        def starttls(self, *, context):
            import ssl

            assert context.verify_mode == ssl.CERT_REQUIRED
            assert context.check_hostname
            self.tls = True

        def login(self, username, password):
            assert self.tls
            assert username == "sender@example.test"
            assert password == "synthetic-current-mail-password"
            self.logged_in = True

        def send_message(self, message, *, from_addr, to_addrs):
            assert self.logged_in
            assert from_addr == "sender@example.test"
            assert "buyer sign-in" in str(message["Subject"])
            content = message.get_content()
            assert "10 minutes" in content
            assert "http" not in content
            code = re.search(r"code: ([0-9]{6})", content)[1]
            messages.append((to_addrs[0], code))
            return {}

    monkeypatch.setattr("src.buyer_auth.mail.smtplib.SMTP", SMTP)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=ORIGIN, headers={"Origin": ORIGIN}
    ) as client:
        yield BrowserFixture(scratch, sessions, app, client, auth, clock, messages)
    await engine.dispose()


async def context(client):
    response = await client.post(PATH + "/context", json={})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return {"X-CSRF-Token": response.json()["csrf_token"]}


async def challenge(buyer, client=None, email="buyer@example.test", headers=None):
    client = client if client is not None else buyer.client
    headers = await context(client) if headers is None else headers
    response = await client.post(PATH + "/email/challenges", json={"email": email}, headers=headers)
    assert response.status_code == 202, response.text
    return response.json()["challenge_id"], buyer.messages[-1][1], headers


async def verify(client, proof, *, code=None):
    handle, known, headers = proof
    return await client.post(
        PATH + "/email/verify",
        json={"challenge_id": handle, "code": known if code is None else code},
        headers=headers,
    )


async def signed_in(buyer, client=None, email="buyer@example.test"):
    client = client if client is not None else buyer.client
    proof = await challenge(buyer, client, email)
    response = await verify(client, proof)
    assert response.status_code == 200, response.text
    return response, proof


def test_email_equality_and_rejected_syntax():
    assert normalize_email(" Mixed.Case+tag@Example.Test ") == "mixed.case+tag@example.test"
    assert normalize_email("a.b@example.test") != normalize_email("ab@example.test")
    assert normalize_email("a+tag@example.test") != normalize_email("a@example.test")
    for value in (
        "a\n@example.test",
        " a@example.test\t",
        '"a"@example.test',
        "Name <a@example.test>",
        "a@[127.0.0.1]",
        "a..b@example.test",
        "é@example.test",
        "a@localhost",
        "a@-bad.test",
        "a" * 65 + "@example.test",
    ):
        with pytest.raises(ValueError, match="Invalid email"):
            normalize_email(value)


@pytest.mark.parametrize(
    "changes",
    [
        {"buyer_protection_key": SecretStr("")},
        {"buyer_protection_key": SecretStr("short")},
        {"buyer_public_origin": "http://buyer.test"},
        {"buyer_public_origin": ORIGIN + "/"},
        {"buyer_public_origin": ORIGIN + ":invalid"},
        {"database_echo": True},
        {"smtp_password": SecretStr("")},
        {"smtp_from_email": "sender\n@example.test"},
        {"buyer_sessions_enabled": False},
    ],
)
def test_enabled_configuration_fails_closed_without_network(changes):
    with pytest.raises(ValueError, match=r"configuration|requires|Port"):
        configuration(**changes)


async def test_positive_new_identity_cookie_reuse_and_no_business_side_effect(buyer, monkeypatch):
    monkeypatch.setattr("src.buyer_auth.service.secrets.randbelow", lambda limit: 7)
    response, proof = await signed_in(buyer, email="Mixed.Case+Tag@Example.Test")
    assert proof[1] == "000007"
    data = response.json()
    assert data["user"]["id"] > 0
    assert data["user"]["telegram_id"] is None
    cookie = response.headers["set-cookie"]
    for attribute in ("Secure", "HttpOnly", "SameSite=lax", "Path=/", "Max-Age=7776000"):
        assert attribute in cookie
    assert "Domain=" not in cookie
    session_response = await buyer.client.get(PATH + "/session")
    assert session_response.json() == data
    assert "set-cookie" not in session_response.headers
    assert (await verify(buyer.client, proof)).status_code == 409
    async with buyer.sessions() as db:
        identity = await db.get(BuyerEmailIdentity, "mixed.case+tag@example.test")
        user = await db.get(User, identity.user_id)
        assert user.is_active
        assert not user.is_admin
        assert user.phone_verified_at is None
        assert await db.scalar(select(func.count()).select_from(CustomerProfile)) == 0
        assert await db.scalar(select(func.count()).select_from(PointLedger)) == 0
        row = (await db.scalars(select(BuyerEmailChallenge))).one()
        assert row.state == "consumed"
        assert row.attempts == 1
        assert "000007" not in row.code_digest
        assert row.browser_digest != buyer.client.cookies[CONTEXT_COOKIE]


async def test_default_off_and_each_flag_boundary_with_positive_delivery_control(
    buyer, monkeypatch
):
    await signed_in(buyer)
    token = buyer.client.cookies[SESSION_COOKIE]
    monkeypatch.setattr(buyer.auth.config, "buyer_email_auth_enabled", False)
    before = len(buyer.messages)
    for route in ("/context", "/email/challenges", "/email/verify"):
        response = await buyer.client.post(
            PATH + route, content=b"not JSON", headers={"Origin": "null"}
        )
        assert response.status_code == 404
        assert response.json() == {"error": "buyer_auth_disabled"}
    assert len(buyer.messages) == before
    assert (await buyer.client.get(PATH + "/session")).status_code == 200
    monkeypatch.setattr(buyer.auth.config, "buyer_sessions_enabled", False)
    assert (await buyer.client.get(PATH + "/session")).status_code == 404
    # Durable token revocation remains available while both entry switches are off.
    async with buyer.sessions() as db:
        row = (await db.scalars(select(BuyerSession))).one()
        from src.buyer_auth.security import csrf

        header = csrf(buyer.auth.config, token, generation=row.generation)
    response = await buyer.client.post(PATH + "/logout", json={}, headers={"X-CSRF-Token": header})
    assert response.status_code == 204
    async with buyer.sessions() as db:
        assert (await db.scalars(select(BuyerSession))).one().revoked_at is not None
    defaults = configuration(
        buyer_sessions_enabled=False,
        buyer_email_auth_enabled=False,
        buyer_protection_key=SecretStr(""),
        smtp_password=SecretStr(""),
    )
    assert not defaults.buyer_sessions_enabled


@pytest.mark.parametrize(
    "origin",
    [None, "null", "https://foreign.test", "https://sub.buyer.test", ORIGIN + ".attacker.test"],
)
async def test_origin_refusal_before_context_or_send(buyer, origin):
    headers = {} if origin is None else {"Origin": origin}
    async with AsyncClient(transport=ASGITransport(app=buyer.app), base_url=ORIGIN) as client:
        response = await client.post(PATH + "/context", json={}, headers=headers)
    assert response.status_code == 403
    assert response.json() == {"error": "request_forbidden"}
    assert not buyer.messages
    async with buyer.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(BuyerAuthLimit)) == 0


async def test_body_types_stream_limit_host_and_csrf(buyer):
    response = await buyer.client.post(
        PATH + "/context", json={}, headers={"Host": "evil.test", "X-Forwarded-Host": "buyer.test"}
    )
    assert response.status_code == 403
    assert (await buyer.client.post(PATH + "/context", content="{}")).status_code == 415

    async def chunks():
        yield b"{" + b" " * 2048
        yield b" " * 2048 + b"}"

    response = await buyer.client.post(
        PATH + "/context", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413
    headers = await context(buyer.client)
    first_cookie = buyer.client.cookies[CONTEXT_COOKIE]
    buyer.clock.advance(100)
    assert await context(buyer.client) == headers
    assert buyer.client.cookies[CONTEXT_COOKIE] == first_cookie
    for body in ({"email": 7}, {"email": "a@example.test", "target_user": 1}, {}):
        response = await buyer.client.post(PATH + "/email/challenges", json=body, headers=headers)
        assert response.status_code == 422
        assert response.json() == {"error": "invalid_request"}
    response = await buyer.client.post(PATH + "/email/challenges", json={"email": "a@example.test"})
    assert response.status_code == 403
    for body in ({"challenge_id": 2, "code": "000000"}, {"challenge_id": "x", "code": 0}):
        response = await buyer.client.post(PATH + "/email/verify", json=body, headers=headers)
        assert response.status_code == 422
    for code in ("00000", "１２３４５６", " 12345", "000000"):
        response = await buyer.client.post(
            PATH + "/email/verify", json={"challenge_id": "bad", "code": code}, headers=headers
        )
        assert response.status_code == 401
        assert response.json() == {"error": "invalid_authentication"}
    assert not buyer.messages
    async with buyer.sessions() as db:
        assert sorted(
            len(row.events) for row in (await db.scalars(select(BuyerAuthLimit))).all()
        ) == [1, 6]


async def test_five_attempts_wrong_browser_and_exact_expiry(buyer):
    proof = await challenge(buyer)
    async with buyer.another() as other:
        other_headers = await context(other)
        wrong_browser = (proof[0], proof[1], other_headers)
        assert (await verify(other, wrong_browser)).status_code == 401
    async with buyer.sessions() as db:
        assert (await db.get(BuyerEmailChallenge, proof[0])).attempts == 0
    wrong = "111111" if proof[1] != "111111" else "222222"
    for _ in range(5):
        assert (await verify(buyer.client, proof, code=wrong)).status_code == 401
    assert (await verify(buyer.client, proof)).status_code == 401
    async with buyer.sessions() as db:
        row = await db.get(BuyerEmailChallenge, proof[0])
        assert row.attempts == 5
        assert row.state == "exhausted"
    proof = await challenge(buyer, email="expired@example.test")
    buyer.clock.advance(600)
    assert (await verify(buyer.client, proof)).status_code == 401


async def test_resend_cooldown_persistence_and_pending_off(buyer, monkeypatch):
    proof = await challenge(buyer)
    second_service = BuyerAuthService(
        buyer.auth.config, buyer.sessions, buyer.auth.mailer, buyer.clock
    )
    with pytest.raises(BuyerAuthError) as failure:
        await second_service.reserve(
            "buyer@example.test", buyer.client.cookies[CONTEXT_COOKIE], "different-peer", None
        )
    assert failure.value.status == 429
    assert failure.value.retry == 60
    buyer.clock.advance(60)
    next_proof = await challenge(buyer, headers=proof[2])
    assert (await verify(buyer.client, proof)).status_code == 401
    monkeypatch.setattr(buyer.auth.config, "buyer_email_auth_enabled", False)
    assert (await verify(buyer.client, next_proof)).status_code == 404
    async with buyer.sessions() as db:
        assert (await db.get(BuyerEmailChallenge, proof[0])).state == "superseded"
        assert (await db.get(BuyerEmailChallenge, next_proof[0])).state == "ready"


async def test_parallel_same_proof_single_consume_and_response_loss(buyer):
    proof = await challenge(buyer)
    browser = buyer.client.cookies[CONTEXT_COOKIE]
    gate = asyncio.Event()

    async def consume():
        await gate.wait()
        try:
            return await buyer.auth.verify(proof[0], proof[1], browser, "parallel-peer", None)
        except BuyerAuthError as error:
            return error.status

    tasks = [asyncio.create_task(consume()) for _ in range(2)]
    gate.set()
    results = await asyncio.gather(*tasks)
    assert sum(isinstance(value, tuple) for value in results) == 1
    assert 401 in results
    # Pretend successful response/cookie never reached the browser; fresh proof resolves same ID.
    winner = next(value for value in results if isinstance(value, tuple))
    buyer.clock.advance(60)
    response = await verify(buyer.client, await challenge(buyer))
    assert response.json()["user"] == winner[0]["user"]
    async with buyer.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 1
        assert await db.scalar(select(func.count()).select_from(BuyerEmailIdentity)) == 1
        assert await db.scalar(select(func.count()).select_from(BuyerSession)) == 2


async def test_two_browsers_new_email_race_no_orphan(buyer):
    first = await challenge(buyer)
    buyer.clock.advance(60)
    async with buyer.another() as other:
        second = await challenge(buyer, other, "BUYER@EXAMPLE.TEST")
        a, b = await asyncio.gather(verify(buyer.client, first), verify(other, second))
    assert a.status_code == b.status_code == 200
    assert a.json()["user"] == b.json()["user"]
    async with buyer.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(User)) == 1
        assert await db.scalar(select(func.count()).select_from(BuyerEmailIdentity)) == 1
        assert await db.scalar(select(func.count()).select_from(BuyerSession)) == 2


async def test_session_rotate_logout_other_browser_and_absolute_idle(buyer):
    response, _proof = await signed_in(buyer)
    original = response.json()
    old = buyer.client.cookies[SESSION_COOKIE]
    assert (await buyer.client.post(PATH + "/session/rotate", json={})).status_code == 403
    rotated = await buyer.client.post(
        PATH + "/session/rotate", json={}, headers={"X-CSRF-Token": original["csrf_token"]}
    )
    assert rotated.status_code == 200
    assert rotated.json()["authenticated_at"] == original["authenticated_at"]
    assert rotated.json()["csrf_token"] != original["csrf_token"]
    async with buyer.another() as other:
        other.cookies.set(SESSION_COOKIE, old, domain="buyer.test", path="/")
        assert (await other.get(PATH + "/session")).status_code == 401
    new_token = buyer.client.cookies[SESSION_COOKIE]
    current = rotated.json()
    assert (await buyer.client.post(PATH + "/logout", json={})).status_code == 403
    assert (
        await buyer.client.post(
            PATH + "/logout", json={}, headers={"X-CSRF-Token": current["csrf_token"]}
        )
    ).status_code == 204
    assert (await buyer.auth.logout(None, None)) is None
    with pytest.raises(BuyerAuthError):
        await buyer.auth.use_session(new_token)
    buyer.clock.advance(60)
    await signed_in(buyer)
    token = buyer.client.cookies[SESSION_COOKIE]
    buyer.clock.advance(30 * 86400)
    with pytest.raises(BuyerAuthError):
        await buyer.auth.use_session(token)
    # Separate fresh browser continues to the fixed absolute boundary despite rolling uses.
    buyer.client.cookies.clear()
    await signed_in(buyer, email="absolute@example.test")
    for _ in range(3):
        buyer.clock.advance(29 * 86400)
        assert (await buyer.client.get(PATH + "/session")).status_code == 200
    buyer.clock.advance(3 * 86400)
    assert (await buyer.client.get(PATH + "/session")).status_code == 401


async def test_current_user_activity_and_authority_separation(buyer):
    response, _proof = await signed_in(buyer)
    uid = response.json()["user"]["id"]
    async with buyer.sessions.begin() as db:
        user = await db.get(User, uid)
        user.is_admin = True
    assert (await buyer.client.get(PATH + "/session")).status_code == 200
    assert (await buyer.client.get("/api/v1/users/me")).status_code == 401
    assert (await buyer.client.get("/api/v1/admin/auth/me")).status_code == 401
    assert (await buyer.client.post("/api/v1/auth/register", json={})).status_code == 404
    async with buyer.sessions.begin() as db:
        user = await db.get(User, uid)
        user.is_active = False
    assert (await buyer.client.get(PATH + "/session")).status_code == 401
