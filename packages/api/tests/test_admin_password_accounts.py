"""T1-T3: real PG writes and real password/code HTTP boundaries."""

import asyncio
import contextlib
import json
import os
import subprocess
import sys
import threading
import time

import bcrypt
import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from scripts import admin_accounts as cli
from src.auth import admin_router as auth
from src.auth.accounts import AccountRefused, change_account, create_account
from src.auth.dependencies import require_admin
from src.auth.models import AdminPasswordAccount
from src.auth.passwords import (
    DUMMY_HASH,
    PasswordServiceUnavailable,
    VerificationCapacity,
    check_hash,
    make_hash,
    normalize_login,
    password_bytes,
)
from src.config import settings
from src.database import get_db
from src.main import app
from src.users.models import User

URL = "/api/v1/admin/auth"
PASSWORD = "synthetic pass word "


@pytest.fixture(autouse=True)
def clean_auth(monkeypatch):
    auth.LOGIN_ATTEMPTS.clear()
    auth.AUTH_CODES.clear()
    monkeypatch.setattr(auth, "_verification_capacity", VerificationCapacity())
    monkeypatch.setattr(settings, "admin_password", SecretStr(""))
    monkeypatch.setattr(settings, "admin_telegram_id", 99001)
    yield
    auth.LOGIN_ATTEMPTS.clear()
    auth.AUTH_CODES.clear()


async def seed(db, *, active=True, admin=False, user_id=901):
    user = User(
        id=user_id,
        telegram_id=99000 + user_id,
        first_name="Synthetic",
        username="original",
        is_active=active,
        is_admin=admin,
    )
    db.add(user)
    await db.commit()
    return user


async def account(db, password=PASSWORD):
    user = await seed(db)
    await create_account(db, " Fictional ", user.telegram_id, make_hash(password))
    await db.commit()
    return user


async def login(client, username="fictional", password=PASSWORD):
    return await client.post(
        URL + "/login",
        content=json.dumps({"username": username, "password": password}),
        headers={"Content-Type": "application/json"},
    )


async def test_create_login_me_protected_reset_and_revoke(client, test_db):
    user = await account(test_db)
    stored = await test_db.get(AdminPasswordAccount, user.id)
    assert stored.login == "fictional"
    assert stored.password_hash.startswith("$2b$12$")
    assert PASSWORD not in repr(stored)
    reply = await login(client, " \tFICTIONAL\n")
    assert reply.status_code == 200
    body = reply.json()
    assert set(body) == {"access_token", "token_type", "user"}
    assert body["token_type"] == "bearer"
    assert body["user"] == {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "username": "original",
        "first_name": "Synthetic",
        "last_name": None,
        "is_admin": True,
    }
    payload = jwt.decode(
        body["access_token"],
        settings.secret_key.get_secret_value(),
        algorithms=[settings.algorithm],
    )
    assert payload["sub"] == str(user.id)
    assert type(payload["exp"]) is int
    assert payload["type"] == "admin"
    assert payload["telegram_id"] == user.telegram_id
    assert "password_hash" not in payload
    headers = {"Authorization": "Bearer " + body["access_token"]}
    me = await client.get(URL + "/me", headers=headers)
    assert me.status_code == 200
    assert set(me.json()) == {
        "id",
        "telegram_id",
        "username",
        "first_name",
        "last_name",
    }
    assert (await client.get("/api/v1/admin/orders", headers=headers)).status_code == 200
    before = stored.password_hash
    await change_account(test_db, "fictional", hashed=make_hash("new synthetic password"))
    await test_db.commit()
    assert (await login(client)).status_code == 401
    assert (await login(client, password="new synthetic password")).status_code == 200
    assert (await client.get(URL + "/me", headers=headers)).status_code == 200
    await change_account(test_db, "fictional")
    await test_db.commit()
    assert (await client.get(URL + "/me", headers=headers)).status_code == 403
    assert (await login(client, password="new synthetic password")).status_code == 401
    await change_account(test_db, "fictional")
    user.is_active = False
    await test_db.commit()
    await change_account(test_db, "fictional", hashed=make_hash("inactive reset password"))
    await test_db.commit()
    await test_db.refresh(user)
    assert not user.is_admin
    assert not user.is_active
    assert user.username == "original"
    assert stored.password_hash != before


async def test_create_conflicts_missing_identity_and_rollback(test_db):
    user = await account(test_db)
    other = await seed(test_db, user_id=902)
    for name, identity in [
        ("fictional", other.telegram_id),
        ("other-name", user.telegram_id),
        ("fictional", user.telegram_id),
    ]:
        with pytest.raises(AccountRefused):
            await create_account(test_db, name, identity, DUMMY_HASH)
        await test_db.rollback()
        await test_db.refresh(user)
        await test_db.refresh(other)
    assert not other.is_admin

    async def failing_transaction():
        await create_account(test_db, "rollback", other.telegram_id, DUMMY_HASH)
        raise RuntimeError("induced rollback")

    with pytest.raises(RuntimeError, match="induced rollback"):
        await failing_transaction()
    await test_db.rollback()
    await test_db.refresh(other)
    assert not other.is_admin
    assert (
        await test_db.scalar(
            select(AdminPasswordAccount).where(AdminPasswordAccount.login == "rollback")
        )
        is None
    )
    for identity in (999999, 0, 9223372036854775808):
        with pytest.raises(AccountRefused):
            await create_account(test_db, "missing", identity, DUMMY_HASH)
        await test_db.rollback()
    inactive = await seed(test_db, active=False, user_id=903)
    with pytest.raises(AccountRefused):
        await create_account(test_db, "inactive", inactive.telegram_id, DUMMY_HASH)
    await test_db.rollback()
    for hashed in (None, DUMMY_HASH):
        with pytest.raises(AccountRefused):
            await change_account(test_db, "missing", hashed=hashed)
        await test_db.rollback()


@pytest.mark.parametrize("same_user", [True, False])
async def test_concurrent_create_has_one_complete_winner(test_db, same_user):
    await seed(test_db)
    await seed(test_db, user_id=902)
    sessions = async_sessionmaker(test_db.bind, expire_on_commit=False)

    async def create(index):
        try:
            async with sessions.begin() as db:
                await create_account(
                    db,
                    "shared" if not same_user else f"account-{index}",
                    99901 if same_user else 99901 + index,
                    DUMMY_HASH,
                )
            return True
        except (AccountRefused, IntegrityError):
            return False

    outcomes = await asyncio.wait_for(asyncio.gather(create(0), create(1)), timeout=10)
    assert sorted(outcomes) == [False, True]
    await test_db.rollback()
    rows = list(await test_db.scalars(select(AdminPasswordAccount)))
    assert len(rows) == 1
    assert list(await test_db.scalars(select(User.id).where(User.is_admin))) == [rows[0].user_id]


@pytest.mark.parametrize("value", ["ab", "b iotact", "biotact", "Оksana", "abc\u00a0", "a" * 65])
def test_operator_login_bounds(value):
    with pytest.raises(ValueError, match="Invalid login"):
        normalize_login(value)


def test_hash_salts_and_byte_bounds():
    assert normalize_login(" \tFiCTional\r\n") == "fictional"
    assert password_bytes("я" * 36) == ("я" * 36).encode()
    for value in ("я" * 37, "x" * 73, "\ud800", "", "short"):
        with pytest.raises(ValueError, match="Invalid password"):
            password_bytes(value, creating=True)
    first = make_hash(PASSWORD)
    second = make_hash(PASSWORD)
    assert first != second
    assert bcrypt.checkpw(PASSWORD.encode(), first.encode())


@pytest.mark.parametrize(
    ("username", "password"),
    [
        ("missing", PASSWORD),
        ("fictional", "wrong"),
        ("ab", PASSWORD),
        ("Оksana", PASSWORD),
        ("fictional", ""),
        ("fictional", "я" * 37),
        ("fictional", "\ud800"),
    ],
)
async def test_credential_failures_are_generic(client, test_db, username, password):
    await account(test_db)
    response = await login(client, username, password)
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid username or password"}
    assert response.headers["cache-control"] == "no-store"
    assert len(list(await test_db.scalars(select(AdminPasswordAccount)))) == 1


@pytest.mark.parametrize("field", ["is_admin", "is_active"])
async def test_rights_login_does_not_restore(client, test_db, field):
    user = await account(test_db)
    await test_db.execute(update(User).where(User.id == user.id).values(**{field: False}))
    await test_db.commit()
    assert (await login(client)).status_code == 401
    await test_db.refresh(user)
    assert not getattr(user, field)


async def test_exact_72_bytes_and_preserved_spaces(client, test_db):
    await account(test_db, "я" * 36)
    assert (await login(client, password="я" * 36)).status_code == 200
    assert (await login(client, password="я" * 37)).status_code == 401
    await change_account(test_db, "fictional", hashed=make_hash(PASSWORD))
    await test_db.commit()
    assert (await login(client, password=PASSWORD.rstrip())).status_code == 401
    assert (await login(client)).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        {"username": "fictional", "password": 123},
        {"username": 1, "password": PASSWORD},
        {"username": "x" * 129, "password": PASSWORD},
        {"username": "fictional", "password": PASSWORD, "extra": "canary"},
    ],
)
async def test_strict_wire_does_not_charge_throttle(client, body):
    response = await client.post(URL + "/login", json=body)
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid authentication request"}
    assert not auth.LOGIN_ATTEMPTS


async def test_malformed_and_chunked_oversize(client):
    assert (await client.post(URL + "/login", content="{canary")).status_code == 422

    async def chunks():
        yield b"x" * 2048
        yield b"y" * 2049

    response = await client.post(URL + "/login", content=chunks())
    assert response.status_code == 413
    assert response.json() == {"detail": "Authentication request too large"}
    assert not auth.LOGIN_ATTEMPTS


async def test_throttle_lockout_expiry_and_success_reset(client, test_db, monkeypatch):
    await account(test_db)
    clock = [time.time()]
    monkeypatch.setattr(auth.time, "time", lambda: clock[0])
    for _ in range(5):
        assert (await login(client, "ab")).status_code == 401
    response = await login(client)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "900"
    clock[0] += 901
    assert (await login(client)).status_code == 200
    assert (await login(client, "ab")).status_code == 401
    assert (await login(client)).status_code == 200
    assert not auth.LOGIN_ATTEMPTS["127.0.0.1"]["attempts"]


@contextlib.asynccontextmanager
async def peer_client(db, peer, asgi=None):
    """An HTTP client whose connection comes from ``peer``; ``asgi`` wraps the app like the server."""

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    try:
        transport = ASGITransport(app=asgi or app, client=(peer, 50000))
        async with AsyncClient(transport=transport, base_url="http://test") as http:
            yield http
    finally:
        app.dependency_overrides.pop(get_db, None)


def attempt(password, headers):
    body = json.dumps({"username": "fictional", "password": password})
    return {"content": body, "headers": {"Content-Type": "application/json", **headers}}


def through_nginx(real, sent):
    """Headers as production nginx forwards them: X-Real-IP overwritten, X-Forwarded-For appended."""
    return {"X-Real-IP": real, "X-Forwarded-For": f"{sent}, {real}"}


@pytest.mark.parametrize("stack", ["trusted-proxy-peers", "uvicorn-proxy-headers"])
async def test_s1_through_proxy_limit_keys_on_address_the_proxy_wrote(test_db, monkeypatch, stack):
    """Audit S1 invariant 1: behind nginx, client-sent forwarding values do not move the key.

    ``trusted-proxy-peers``: the app sees the nginx peer, listed in BUYER_TRUSTED_PROXY_PEERS.
    ``uvicorn-proxy-headers``: the list is empty and the app runs behind uvicorn's default proxy
    header handling (trusts 127.0.0.1), as ecosystem.config.js launches it.
    """
    await account(test_db)
    asgi = None
    if stack == "trusted-proxy-peers":
        monkeypatch.setattr(settings, "buyer_trusted_proxy_peers", ["127.0.0.1/32"])
    else:
        asgi = ProxyHeadersMiddleware(app, trusted_hosts="127.0.0.1")
    client_ip, other_ip = "203.0.113.7", "203.0.113.8"
    async with peer_client(test_db, "127.0.0.1", asgi) as http:
        for index in range(5):
            sent = through_nginx(client_ip, f"198.51.100.{index}")
            assert (await http.post(URL + "/login", **attempt("ab", sent))).status_code == 401
        sent = through_nginx(client_ip, "198.51.100.99")
        assert (await http.post(URL + "/login", **attempt(PASSWORD, sent))).status_code == 429
        # Another client that names the locked address in its own header is not locked.
        sent = through_nginx(other_ip, client_ip)
        assert (await http.post(URL + "/login", **attempt(PASSWORD, sent))).status_code == 200
    assert set(auth.LOGIN_ATTEMPTS) == {client_ip, other_ip}


async def test_s1_direct_connection_ignores_forwarding_headers(test_db, monkeypatch):
    """Audit S1 invariant 2: a peer that is not a trusted proxy is the key; headers are ignored."""
    await account(test_db)
    monkeypatch.setattr(settings, "buyer_trusted_proxy_peers", ["127.0.0.1/32"])
    direct = "192.0.2.50"
    async with peer_client(test_db, direct) as http:
        for index in range(5):
            sent = {"X-Real-IP": f"198.51.100.{index}", "X-Forwarded-For": f"198.51.100.{index}"}
            assert (await http.post(URL + "/login", **attempt("ab", sent))).status_code == 401
        sent = {"X-Real-IP": "198.51.100.99", "X-Forwarded-For": "198.51.100.98"}
        assert (await http.post(URL + "/login", **attempt(PASSWORD, sent))).status_code == 429
    assert set(auth.LOGIN_ATTEMPTS) == {direct}


async def test_capacity_echo_and_lockout_precede_sql(client, test_db, monkeypatch):
    def no_sql(*args, **kwargs):
        pytest.fail("credential lookup reached")

    monkeypatch.setattr(test_db, "scalar", no_sql)
    with contextlib.ExitStack() as stack:
        for _ in range(4):
            stack.enter_context(auth._verification_capacity.admit())
        assert (await login(client)).status_code == 503
    assert not auth.LOGIN_ATTEMPTS
    monkeypatch.setattr(settings, "database_echo", True)
    assert (await login(client)).status_code == 503
    for _ in range(5):
        auth.record_login_attempt("127.0.0.1", False)
    assert (await login(client)).status_code == 429


async def test_cancelled_verifier_holds_slot_until_worker_completion(monkeypatch):
    started = threading.Event()
    finish = threading.Event()
    capacity = VerificationCapacity()

    def blocking(*args):
        started.set()
        assert finish.wait(5)
        return True

    monkeypatch.setattr(bcrypt, "checkpw", blocking)

    async def verify():
        with capacity.admit():
            return await check_hash(b"fictional", DUMMY_HASH)

    worker = asyncio.create_task(verify())
    assert await asyncio.to_thread(started.wait, 2)
    worker.cancel()
    await asyncio.sleep(0)
    assert capacity.in_flight == 1
    assert not worker.done()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await worker
    assert capacity.in_flight == 0
    with pytest.raises(PasswordServiceUnavailable):
        await check_hash(b"x", "corrupt-hash")

    def broken(*args):
        raise ValueError("synthetic worker payload")

    monkeypatch.setattr(bcrypt, "checkpw", broken)
    with pytest.raises(PasswordServiceUnavailable):
        await verify()
    assert capacity.in_flight == 0


@pytest.mark.parametrize("state", ["good", "missing", "revoked", "inactive", "disabled", "wrong"])
async def test_compat_never_creates_or_promotes(client, test_db, monkeypatch, state):
    monkeypatch.setattr(settings, "admin_password", SecretStr("compat synthetic"))
    if state != "missing":
        test_db.add(
            User(
                id=901,
                telegram_id=99001,
                first_name="Original",
                is_admin=state != "revoked",
                is_active=state != "inactive",
            )
        )
        await test_db.commit()
    if state == "disabled":
        monkeypatch.setattr(settings, "admin_password", SecretStr(""))
    response = await login(client, " Biotact ", "wrong" if state == "wrong" else "compat synthetic")
    assert response.status_code == (200 if state == "good" else 401)
    user = await test_db.get(User, 901)
    assert (user is None) == (state == "missing")
    if user:
        assert user.first_name == "Original"
        assert user.is_admin == (state != "revoked")


async def test_code_whitelist_rights_consumption_storage_retry(client, test_db, monkeypatch):
    user = await seed(test_db, admin=True)
    monkeypatch.setattr(auth, "ADMIN_TELEGRAM_IDS", {user.telegram_id})
    monkeypatch.setattr(settings, "telegram_bot_token", "synthetic-bot")

    async def code():
        value = (await client.post(URL + "/code")).json()["code"]
        assert (await client.get(URL + "/code/" + value)).json() == {
            "status": "pending",
            "token": None,
            "user": None,
        }
        verify = await client.post(
            URL + "/verify",
            headers={"X-Bot-Token": "synthetic-bot"},
            json={"code": value, "telegram_id": user.telegram_id, "first_name": "Do not overwrite"},
        )
        assert verify.status_code == 200
        return value

    value = await code()
    original = test_db.execute

    async def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic storage payload")

    monkeypatch.setattr(test_db, "execute", unavailable)
    assert (await client.get(URL + "/code/" + value)).status_code == 503
    assert value in auth.AUTH_CODES
    monkeypatch.setattr(test_db, "execute", original)
    assert (await client.get(URL + "/code/" + value)).json()["status"] == "verified"
    assert value not in auth.AUTH_CODES
    assert user.first_name == "Synthetic"
    value = await code()
    user.is_admin = False
    await test_db.commit()
    assert (await client.get(URL + "/code/" + value)).status_code == 403
    assert value not in auth.AUTH_CODES
    user.is_admin = True
    await test_db.commit()
    value = await code()
    monkeypatch.setattr(auth, "ADMIN_TELEGRAM_IDS", set())
    assert (await client.get(URL + "/code/" + value)).status_code == 403
    assert value not in auth.AUTH_CODES
    assert (await client.get(URL + "/code/unknown")).json()["status"] == "expired"


async def test_guard_refreshes_reused_identity_map(test_db):
    user = await seed(test_db, admin=True)
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=auth.create_admin_token(user.id, user.telegram_id)
    )
    assert (await require_admin(test_db, credentials)).id == user.id
    sessions = async_sessionmaker(test_db.bind)
    async with sessions.begin() as db:
        await db.execute(update(User).where(User.id == user.id).values(is_admin=False))
    assert user.is_admin
    with pytest.raises(HTTPException) as refusal:
        await require_admin(test_db, credentials)
    assert refusal.value.status_code == 403
    assert not user.is_admin


@pytest.mark.parametrize(
    "argv",
    [
        ["create", "--login", "synthetic-canary"],
        ["unknown-canary"],
        ["revoke", "--login", "biotact"],
        ["create", "--login", "fictional", "--telegram-id", "bad-canary"],
        ["create", "--login", "fictional", "--telegram-id", "0"],
        ["create", "--login", "fictional", "--telegram-id", "9" * 20],
    ],
)
def test_cli_parser_refusals_do_not_echo(argv, capsys):
    assert cli.main(argv) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Account operation refused.\n"


async def test_cli_real_operations_service_failure(test_db, monkeypatch, capsys):
    user = await seed(test_db)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: PASSWORD)
    for command in (
        ["create", "--login", "Cli-Account", "--telegram-id", str(user.telegram_id)],
        ["set-password", "--login", "cli-account"],
        ["revoke", "--login", "cli-account"],
    ):
        assert await asyncio.to_thread(cli.main, command) == 0
    await test_db.refresh(user)
    assert not user.is_admin
    assert user.is_active
    assert (
        await asyncio.to_thread(
            cli.main, ["create", "--login", "cli-account", "--telegram-id", str(user.telegram_id)]
        )
        == 2
    )

    async def bad(*args, **kwargs):
        raise RuntimeError("synthetic secret exception")

    monkeypatch.setattr(cli, "operate", bad)
    assert await asyncio.to_thread(cli.main, ["revoke", "--login", "cli-account"]) == 1
    output = capsys.readouterr()
    assert "synthetic secret" not in output.out + output.err
    assert PASSWORD not in output.out + output.err
    assert "Account service unavailable." in output.err


@pytest.mark.parametrize("failure", ["nonterminal", "fallback", "mismatch", "cancel", "short"])
def test_cli_hidden_input_refusals(monkeypatch, capsys, failure):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: failure != "nonterminal")
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    values = iter([PASSWORD, "mismatch" if failure == "mismatch" else PASSWORD])

    def getpass(prompt):
        if failure == "fallback":
            raise cli.getpass.GetPassWarning("synthetic-canary")
        if failure == "cancel":
            raise KeyboardInterrupt()
        if failure == "short":
            return "short"
        return next(values)

    monkeypatch.setattr(cli.getpass, "getpass", getpass)
    assert cli.main(["set-password", "--login", "fictional"]) == 2
    assert capsys.readouterr().err == "Account operation refused.\n"


def test_cli_help_before_configuration():
    env = dict(os.environ, DATABASE_URL="invalid-canary")
    result = subprocess.run(
        [sys.executable, "scripts/admin_accounts.py", "--help"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0
    assert "create" in result.stdout
    assert not result.stderr


async def test_unknown_login_uses_fixed_dummy_and_corrupt_hash_is_service_refusal(
    client, monkeypatch
):
    observed = []
    original = auth.check_hash

    async def observe(value, stored):
        observed.append(stored)
        return await original(value, stored)

    monkeypatch.setattr(auth, "check_hash", observe)
    assert (await login(client, "unknown-login")).status_code == 401
    assert observed == [DUMMY_HASH]
    auth.LOGIN_ATTEMPTS.clear()

    async def corrupt(value, stored):
        return await original(value, "corrupt-hash")

    monkeypatch.setattr(auth, "check_hash", corrupt)
    assert (await login(client)).status_code == 503
    assert not auth.LOGIN_ATTEMPTS


@pytest.mark.parametrize("state", ["missing", "inactive", "not-whitelisted"])
async def test_final_code_issuance_refuses_missing_rights(client, test_db, monkeypatch, state):
    if state != "missing":
        await seed(test_db, admin=True, active=state != "inactive")
    monkeypatch.setattr(
        auth, "ADMIN_TELEGRAM_IDS", {99901} if state != "not-whitelisted" else set()
    )
    value = "ABCDEF"
    auth.AUTH_CODES[value] = {"created_at": time.time(), "telegram_id": 99901, "user_data": {}}
    response = await client.get(URL + "/code/" + value)
    assert response.status_code == 403
    assert response.json() == {"detail": "Access denied. You are not an admin."}
    assert value not in auth.AUTH_CODES
    monkeypatch.setattr(settings, "telegram_bot_token", "synthetic-bot")
    response = await client.post(
        URL + "/verify",
        headers={"X-Bot-Token": "wrong"},
        json={"code": value, "telegram_id": 99901, "first_name": "Synthetic"},
    )
    assert response.status_code == 401
