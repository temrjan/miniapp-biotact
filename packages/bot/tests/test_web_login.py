"""Real dispatcher/Redis/API/SQL path; only Telegram delivery is external stubbed I/O."""

import asyncio
import importlib
import json
import logging
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.core.config import settings
from bot.handlers import common, web_login
from bot.referral_entry import INTENT_KEY, parse_intent
from tests.test_employee_referral_entry import A, h  # noqa: F401

API_ROOT = Path(__file__).resolve().parents[2] / "api"


@pytest.fixture
async def integrated(h, monkeypatch):  # noqa: F811
    harness = h
    if not os.environ.get("TG1_API_INTEGRATION"):
        pytest.skip("Explicit TG1_API_INTEGRATION and synthetic PostgreSQL required")
    import psycopg2
    from psycopg2 import sql

    sys.path.insert(0, str(API_ROOT))
    main = importlib.import_module("src.main")
    config_module = importlib.import_module("src.config")
    admin = psycopg2.connect(os.environ["S1_TEST_DATABASE_URL"])
    admin.autocommit = True
    name = "tg1_bot_" + uuid4().hex
    with admin.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = os.environ["DATABASE_URL"].rsplit("/", 1)[0] + "/" + name
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=API_ROOT,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    config = config_module.Settings(
        _env_file=None,
        database_url=url,
        secret_key="synthetic-admin",  # noqa: S106 -- synthetic fixture
        buyer_sessions_enabled=True,
        buyer_telegram_auth_enabled=True,
        buyer_protection_key="synthetic-buyer-key-at-least-32-bytes",
        buyer_public_origin="https://buyer.test",
        buyer_telegram_bot_username="synthetic_bot",
        telegram_bot_token=settings.bot_token.get_secret_value(),
    )
    monkeypatch.setattr(main, "settings", config)
    monkeypatch.setattr(main, "async_session_maker", sessions)
    app = main.create_app()
    monkeypatch.setattr(settings, "buyer_telegram_auth_enabled", True)
    original = httpx.AsyncClient

    def client(**kwargs):
        # Actual internal API routing/validation/transactions; no HTTP response mock.
        return original(
            transport=httpx.ASGITransport(app=app),
            base_url="https://buyer.test",
            headers={"Origin": "https://buyer.test"},
        )

    monkeypatch.setattr(web_login.httpx, "AsyncClient", client)
    browser = client()
    response = await browser.post("/api/v1/buyer/auth/context", json={})
    headers = {"X-CSRF-Token": response.json()["csrf_token"]}
    response = await browser.post(
        "/api/v1/buyer/auth/telegram/requests", json={"request_key": "a" * 32}, headers=headers
    )
    assert response.status_code == 202
    harness.attempt = response.json()
    harness.browser, harness.headers = browser, headers
    harness.sessions, harness.engine = sessions, engine
    harness.app = app
    try:
        yield harness
    finally:
        await browser.aclose()
        await engine.dispose()
        sys.path.remove(str(API_ROOT))
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        admin.close()


@pytest.mark.parametrize(
    ("retry", "delay", "cancel"), [(90, 0, False), (55, 8, False), (90, 0, True)]
)
async def test_dispatcher_retry_after_is_shared_durable_and_has_no_fallback(
    integrated, monkeypatch, caplog, retry, delay, cancel
):
    from src.buyer_auth.telegram import TelegramAuthService

    harness = integrated
    at = [harness.app.state.buyer_auth.clock()]
    harness.app.state.buyer_auth.clock = lambda: at[0]
    await harness.state.set_state(common.RegistrationState.waiting_phone)
    await harness.state.set_data({INTENT_KEY: parse_intent("ref_39_" + A), "lang": "ru"})
    before = await harness.state.get_state(), await harness.state.get_data()
    original = harness.session.make_request
    throttled = False
    entered, release = asyncio.Event(), asyncio.Event()

    async def transport(bot, method, timeout=None):  # noqa: ASYNC109 -- aiogram interface
        nonlocal throttled
        if method.__api_method__ == "sendMessage" and not throttled:
            throttled = True
            harness.session.calls.append(method)
            harness.session.timeouts.append(timeout)
            entered.set()
            if cancel:
                await release.wait()
            at[0] += timedelta(seconds=delay)
            # Use the installed aiogram parser, then actual dispatcher exception routing.
            return harness.session.check_response(
                bot,
                method,
                429,
                json.dumps(
                    {
                        "ok": False,
                        "error_code": 429,
                        "description": "synthetic-private-throttle-canary",
                        "parameters": {"retry_after": retry},
                    }
                ),
            )
        return await original(bot, method, timeout)

    monkeypatch.setattr(harness.session, "make_request", transport)
    text = "/start web_" + harness.attempt["request_id"]
    with caplog.at_level(logging.DEBUG):
        if cancel:
            owner = asyncio.create_task(harness.feed(text))
            try:
                await asyncio.wait_for(entered.wait(), 10)
                owner.cancel()
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await owner
        else:
            await harness.feed(text)
        # The failed prompt is the only Telegram call; no immediate fixed fallback.
        assert len(harness.session.calls) == 1
        # Reconstruct the API owner. Only committed PostgreSQL accounting survives.
        auth = harness.app.state.buyer_auth
        harness.app.state.buyer_telegram_auth = TelegramAuthService(
            type(auth)(auth.config, harness.sessions, clock=auth.clock)
        )
        at[0] += timedelta(seconds=retry - 1)
        await harness.feed(text)
        assert len(harness.session.calls) == 1
        at[0] += timedelta(seconds=1)
        await harness.feed(text)
        assert len(harness.session.calls) == 2
        assert harness.attempt["comparison"] in harness.session.calls[-1].text
        if not cancel and delay == 0:
            # The middleware owns throttles from fixed non-prompt web messages too.
            throttled = False
            await harness.feed("/start web_malformed-private-canary")
            assert len(harness.session.calls) == 3
            at[0] += timedelta(seconds=retry - 1)
            await harness.feed(text)
            assert len(harness.session.calls) == 3
        logging.getLogger("positive-control").info("retry-after-control-survives")
    assert (await harness.state.get_state(), await harness.state.get_data()) == before
    assert "synthetic-private-throttle-canary" not in caplog.text
    assert harness.attempt["request_id"] not in caplog.text
    assert settings.bot_token.get_secret_value() not in caplog.text
    assert "retry-after-control-survives" in caplog.text
    assert all(timeout == 10 for timeout in harness.session.timeouts)


@pytest.mark.parametrize("seed", ["language", "phone", "qr", "combined"])
async def test_web_preserves_real_registration_and_resumes(integrated, seed, caplog):
    harness = integrated
    payload = {"registration_language": True, "lang": "ru", "generation": 77}
    if seed in {"qr", "combined"}:
        payload["loyalty_code"] = "SYNTHETIC"
    if seed == "combined":
        payload[INTENT_KEY] = parse_intent("ref_39_" + A)
    await harness.state.set_state(common.RegistrationState.waiting_phone)
    await harness.state.set_data(payload)
    before = await harness.state.get_state(), await harness.state.get_data()
    with caplog.at_level(logging.DEBUG):
        await harness.feed("/start web_" + harness.attempt["request_id"])
        assert (await harness.state.get_state(), await harness.state.get_data()) == before
        prompt = harness.session.calls[-1]
        assert harness.attempt["comparison"] in prompt.text
        assert prompt.parse_mode is None
        await harness.feed(callback="web:" + harness.attempt["request_id"] + ":confirm")
        await harness.feed(callback="web:" + harness.attempt["request_id"] + ":confirm")
        await harness.feed(callback="web:" + harness.attempt["request_id"] + ":decline")
        await harness.feed("/start web_malformed-private-canary")
        assert (await harness.state.get_state(), await harness.state.get_data()) == before
        result = await harness.browser.post(
            "/api/v1/buyer/auth/telegram/complete",
            json={"request_id": harness.attempt["request_id"]},
            headers=harness.headers,
        )
        assert result.status_code == 200
        assert result.json()["user"]["telegram_id"] == 10
        harness.session.fail = True
        await harness.feed("/start web_" + harness.attempt["request_id"])
        harness.session.fail = False
        assert (await harness.state.get_state(), await harness.state.get_data()) == before
        logging.getLogger("positive-control").info("unrelated-observable")
    assert harness.attempt["request_id"] not in caplog.text
    assert "malformed-private-canary" not in caplog.text
    assert settings.bot_token.get_secret_value() not in caplog.text
    assert "unrelated-observable" in caplog.text
    assert all(timeout == 10 for timeout in harness.session.timeouts)
    assert sum(call.__api_method__ == "answerCallbackQuery" for call in harness.session.calls) == 3
    await harness.feed(callback="skip_phone")
    assert await harness.state.get_state() is None
    if seed == "combined":
        assert harness.urls()[-1].endswith("?ref=" + A)


async def test_missing_and_inaccessible_callback_acknowledged_without_registration(integrated):
    from aiogram.types import Update

    harness = integrated
    await harness.state.set_data(
        {INTENT_KEY: parse_intent("ref_39_" + A), "loyalty_code": "SYNTHETIC"}
    )
    before = await harness.state.get_data()
    for message in (None, {"message_id": 1, "date": 0, "chat": {"id": 10, "type": "private"}}):
        body = {
            "id": uuid4().hex,
            "from": {"id": 10, "is_bot": False, "first_name": "Synthetic"},
            "chat_instance": "synthetic",
            "data": "web:" + harness.attempt["request_id"] + ":confirm",
        }
        if message is not None:
            body["message"] = message
        await harness.dp.feed_update(
            harness.bot, Update.model_validate({"update_id": 10000, "callback_query": body})
        )
    assert sum(call.__api_method__ == "answerCallbackQuery" for call in harness.session.calls) == 2
    assert await harness.state.get_data() == before


async def test_two_browser_contexts_converge_on_uncommitted_ordinary_bot_user(integrated):
    from aiogram.types import User as TelegramUser
    from sqlalchemy import event, func, select
    from src.buyer_auth.models import BuyerSession, BuyerTelegramRequest
    from src.users.models import User

    from bot.services.user import UserService

    harness = integrated
    prefix = "/api/v1/buyer/auth"
    other = type(harness.browser)(
        transport=harness.browser._transport,
        base_url="https://buyer.test",
        headers={"Origin": "https://buyer.test"},
    )
    try:
        second_context = await other.post(prefix + "/context", json={})
        second_headers = {"X-CSRF-Token": second_context.json()["csrf_token"]}
        second = await other.post(
            prefix + "/telegram/requests", json={"request_key": "b" * 32}, headers=second_headers
        )
        assert second.status_code == 202
        await harness.feed("/start web_" + harness.attempt["request_id"])
        await harness.feed(callback="web:" + harness.attempt["request_id"] + ":confirm")
        approved = await other.post(
            "/api/v1/bot/buyer/telegram/decision",
            headers={"X-Bot-Token": settings.bot_token.get_secret_value()},
            json={
                "request_id": second.json()["request_id"],
                "telegram_id": 10,
                "decision": "confirm",
            },
        )
        assert approved.json()["state"] == "approved"
        # The ordinary service's actual INSERT remains uncommitted while both
        # independent TG1 transactions reach their conflicting INSERTs.
        async with harness.sessions() as ordinary:
            user, created = await UserService(ordinary).get_or_create(
                TelegramUser(id=10, is_bot=False, first_name="Synthetic canonical winner")
            )
            assert created
            user.is_admin = True
            user.phone = "synthetic-phone"
            await ordinary.flush()
            user_id = user.id
            reached = asyncio.Event()
            inserts = 0

            def before_insert(_conn, _cursor, statement, _parameters, _context, _many):
                nonlocal inserts
                if statement.startswith("INSERT INTO users"):
                    inserts += 1
                    if inserts == 2:
                        reached.set()

            event.listen(harness.engine.sync_engine, "before_cursor_execute", before_insert)
            tasks = [
                asyncio.create_task(
                    client.post(
                        prefix + "/telegram/complete", json={"request_id": handle}, headers=headers
                    )
                )
                for client, handle, headers in [
                    (harness.browser, harness.attempt["request_id"], harness.headers),
                    (other, second.json()["request_id"], second_headers),
                ]
            ]
            try:
                await asyncio.wait_for(reached.wait(), timeout=10)
                await ordinary.commit()
                results = await asyncio.gather(*tasks)
            finally:
                event.remove(harness.engine.sync_engine, "before_cursor_execute", before_insert)
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        assert all(response.status_code == 200 for response in results)
        assert {response.json()["user"]["id"] for response in results} == {user_id}
        async with harness.sessions() as check:
            assert await check.scalar(select(func.count()).select_from(User)) == 1
            assert await check.scalar(select(func.count()).select_from(BuyerSession)) == 2
            assert set(await check.scalars(select(BuyerTelegramRequest.completed_user_id))) == {
                user_id
            }
            current = await check.get(User, user_id)
            assert current.is_admin
            assert current.is_active
            assert current.phone == "synthetic-phone"
            assert current.first_name == "Synthetic canonical winner"
    finally:
        await other.aclose()
