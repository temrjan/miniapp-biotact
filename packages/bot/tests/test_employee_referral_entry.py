"""Actual Dispatcher routes + own Redis FSM and Telegram API stub; no polling."""

import asyncio
import importlib
import itertools
import logging
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import DefaultKeyBuilder, StorageKey
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import Chat, Message, Update, User

from bot.core.config import settings
from bot.handlers import auth, common, employee_referrals, loyalty, web_login
from bot.middlewares.logging import LoggingMiddleware
from bot.middlewares.referral_entry import ReferralEntryMiddleware
from bot.referral_entry import INTENT_KEY, intent_keyboard, parse_intent

A = "AAAAAAAAAAAAAAAAAAAAAA"
B = "_____________________w"
ids = itertools.count(1)


class StubSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []
        self.timeouts = []
        self.fail = False

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        if False:
            yield b""

    async def make_request(self, bot, method, timeout=None):  # noqa: ASYNC109 -- aiogram interface
        self.calls.append(method)
        self.timeouts.append(timeout)
        if self.fail:
            logging.getLogger("bot-transport-probe").error("PRIVATE " + A + str(method))
            raise RuntimeError("PRIVATE " + A + str(method))
        if method.__api_method__ in {"sendMessage", "editMessageText"}:
            return Message(
                message_id=next(ids),
                date=datetime.now(UTC),
                chat=Chat(id=10, type="private"),
                from_user=User(id=123456789, is_bot=True, first_name="Stub"),
                text="stub",
            )
        return True


@pytest.fixture
async def h(monkeypatch):
    monkeypatch.setattr(settings, "employee_referral_entry_enabled", True)
    service = SimpleNamespace(
        get_or_create=AsyncMock(), update_language=AsyncMock(), update_phone=AsyncMock()
    )
    user = SimpleNamespace(phone=None, language_code="ru", phone_verified_at=None)
    service.get_or_create.return_value = (user, True)
    service.update_language.return_value = user
    service.update_phone.return_value = user
    monkeypatch.setattr(common, "UserService", lambda _: service)
    monkeypatch.setattr(loyalty, "UserService", lambda _: service)
    monkeypatch.setattr(common, "sync_customer_to_crm", AsyncMock())
    monkeypatch.setattr(common, "sync_customer_full", AsyncMock())
    verify = AsyncMock(return_value=(True, "verified"))
    monkeypatch.setattr(auth, "verify_auth_code", verify)
    # Recreate the Router graph per dispatcher; handlers are the real source functions.
    for module in (common, employee_referrals, loyalty, auth, web_login):
        importlib.reload(module)
    monkeypatch.setattr(common, "UserService", lambda _: service)
    monkeypatch.setattr(loyalty, "UserService", lambda _: service)
    monkeypatch.setattr(common, "sync_customer_to_crm", AsyncMock())
    monkeypatch.setattr(common, "sync_customer_full", AsyncMock())
    monkeypatch.setattr(auth, "verify_auth_code", verify)
    # Require an explicit test target; never fall back to the application Redis.
    redis_url = os.environ["BOT_TEST_REDIS_URL"]
    namespace = "g3b-test-" + uuid4().hex
    key_builder = DefaultKeyBuilder(prefix=namespace, with_bot_id=True)
    storage = RedisStorage.from_url(redis_url, key_builder=key_builder, state_ttl=120, data_ttl=120)
    foreign_key = "g3b-isolation-control-" + uuid4().hex
    await storage.redis.set(foreign_key, "outside-fsm", ex=120)
    session = StubSession()
    bot = Bot(settings.bot_token.get_secret_value(), session=session)
    dp = Dispatcher(storage=storage, events_isolation=SimpleEventIsolation())
    dp.update.outer_middleware(ReferralEntryMiddleware())
    dp.update.outer_middleware(LoggingMiddleware())
    dp.include_routers(web_login.router, employee_referrals.router, loyalty.router, auth.router, common.router)
    state = FSMContext(storage, StorageKey(bot_id=bot.id, chat_id=10, user_id=10))

    async def feed(text=None, callback=None, contact=None, chat_type="private"):
        uid = next(ids)
        base = {
            "message_id": uid,
            "date": int(datetime.now(UTC).timestamp()),
            "chat": {"id": 10, "type": chat_type},
            "from": {"id": 10, "is_bot": False, "first_name": "Synthetic"},
        }
        if callback:
            payload = {
                "callback_query": {
                    "id": str(uid),
                    "from": base["from"],
                    "chat_instance": "stub",
                    "data": callback,
                    "message": base,
                }
            }
        else:
            if text:
                base["text"] = text
                base["entities"] = [{"type": "bot_command", "offset": 0, "length": 6}]
            if contact:
                base["contact"] = {
                    "phone_number": "998000000000",
                    "first_name": "Synthetic",
                    "user_id": contact,
                }
            payload = {"message": base}
        return await dp.feed_update(
            bot, Update.model_validate({"update_id": uid, **payload}), session=AsyncMock()
        )

    def urls():
        return [
            button.web_app.url
            for call in session.calls
            if getattr(call, "reply_markup", None) and hasattr(call.reply_markup, "inline_keyboard")
            for row in call.reply_markup.inline_keyboard
            for button in row
            if button.web_app
        ]

    try:
        yield SimpleNamespace(
            feed=feed,
            urls=urls,
            state=state,
            storage=storage,
            redis_url=redis_url,
            foreign_key=foreign_key,
            bot=bot,
            dp=dp,
            service=service,
            user=user,
            session=session,
            verify=verify,
        )
        assert await storage.redis.get(foreign_key) == b"outside-fsm"
    finally:
        owned_keys = [key async for key in storage.redis.scan_iter(match=namespace + ":*")]
        if owned_keys:
            await storage.redis.delete(*owned_keys)
        await storage.redis.delete(foreign_key)
        await dp.fsm.close()
        await bot.session.close()


@pytest.mark.parametrize("payload", ["ref_39_" + A, "ref_9223372036854775807_" + B])
async def test_new_registration_language_skip_latest_button(h, payload):
    await h.feed("/start " + payload)
    assert (await h.state.get_data())[INTENT_KEY] == parse_intent(payload)
    await h.feed(callback="lang:uz")
    await h.feed(callback="skip_phone")
    assert h.urls()[-1] == (
        "https://app.biotact.uz/product/"
        + parse_intent(payload)["product"]
        + "?ref="
        + parse_intent(payload)["token"]
    )
    assert not (await h.state.get_data()).get(INTENT_KEY)
    h.verify.assert_not_called()


async def test_existing_phone_and_off(h, monkeypatch):
    h.service.get_or_create.return_value = (h.user, False)
    h.user.phone = "+998000000000"
    await h.feed("/start ref_39_" + A)
    assert h.urls()[-1].endswith("/product/39?ref=" + A)
    monkeypatch.setattr(settings, "employee_referral_entry_enabled", False)
    await h.feed("/start ref_39_" + A)
    assert h.urls()[-1] == settings.webapp_url


@pytest.mark.parametrize("replacement", ["ref_40_" + B, "ref_40_bad", "", "qr_CODE", "auth_AABBCC"])
async def test_new_start_retires_old_and_late_callbacks_cannot_resurrect(h, replacement):
    await h.feed("/start ref_39_" + A)
    h.service.get_or_create.return_value = (h.user, False)
    await h.feed("/start" + (" " + replacement if replacement else ""))
    await h.feed(callback="lang:ru")
    await h.feed(callback="skip_phone")
    assert not any(A in url for url in h.urls())
    if replacement.startswith("ref_"):
        assert h.urls()[-1].endswith("/product/40?ref=" + (B if replacement.endswith(B) else ""))
    if replacement.startswith("auth_"):
        h.verify.assert_awaited_once()


async def test_contact_own_and_redis_roundtrip(h):
    assert await h.storage.redis.get(h.foreign_key) == b"outside-fsm"
    await h.feed("/start ref_39_" + A)
    await h.feed(callback="lang:ru")
    reloaded = RedisStorage.from_url(h.redis_url, key_builder=h.storage.key_builder)
    try:
        assert (await FSMContext(reloaded, h.state.key).get_data())[INTENT_KEY]["token"] == A
    finally:
        await reloaded.close()
    await h.feed(contact=20)
    assert not h.urls()
    await h.feed(contact=10)
    assert h.urls()[-1].endswith("?ref=" + A)


async def test_group_does_not_launch_or_touch_private_state(h):
    await h.feed("/start ref_39_" + A)
    before = await h.state.get_data()
    # Different actual chat key for group.
    update = Update.model_validate(
        {
            "update_id": next(ids),
            "message": {
                "message_id": 1,
                "date": 1,
                "chat": {"id": -20, "type": "group"},
                "from": {"id": 10, "is_bot": False, "first_name": "Synthetic"},
                "text": "/start ref_39_" + B,
                "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
            },
        }
    )
    await h.dp.feed_update(h.bot, update, session=AsyncMock())
    assert await h.state.get_data() == before
    assert not h.urls()


async def test_serialized_start_and_continuation_current_intent(h):
    h.service.get_or_create.return_value = (h.user, False)
    await h.feed("/start ref_39_" + A)
    entered = asyncio.Event()
    release = asyncio.Event()
    real = h.session.make_request

    async def blocked(bot, method, timeout=None):  # noqa: ASYNC109 -- aiogram interface
        if method.__api_method__ == "sendMessage" and not entered.is_set():
            entered.set()
            await release.wait()
        return await real(bot, method, timeout)

    h.session.make_request = blocked
    second = asyncio.create_task(h.feed("/start ref_40_" + B))
    await asyncio.wait_for(entered.wait(), 3)
    late = asyncio.create_task(h.feed(callback="skip_phone"))
    await asyncio.sleep(0)
    assert not late.done()
    release.set()
    await second
    await late
    assert h.urls()[-1].endswith("/product/40?ref=" + B)
    assert not any(A in u for u in h.urls())


async def test_transport_failure_privacy_and_unrelated_log_control(h, caplog):
    h.user.phone = "+998000000000"
    h.service.get_or_create.return_value = (h.user, False)
    h.session.fail = True
    await h.feed("/start ref_39_" + A)
    assert A not in caplog.text
    assert "PRIVATE" not in caplog.text
    assert "Referral entry" in caplog.text
    logging.getLogger("bot-transport-probe").warning("BOT_ORDINARY_CONTROL")
    assert "BOT_ORDINARY_CONTROL" in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        "ref_039_" + A,
        "ref_9223372036854775808_" + A,
        "ref_39_" + A + " extra",
        "ref_39_bad",
        "ref_39_" + ("A" * 21 + "B"),
        "ref_39_" + ("A" * 65),
        "ref_39_https://evil.invalid",
    ],
)
def test_parser_invalid_marker(payload):
    assert parse_intent(payload)["token"] == ""


def test_fixed_suffix_and_origin_validation(monkeypatch):
    assert parse_intent("ref_39_" + B) == {"product": "39", "token": B}
    assert parse_intent("ref_39_bad") == {"product": "39", "token": ""}
    for url in [
        "http://example.invalid",
        "https://x.invalid/?ref=bad",
        "https://u:p@x.invalid",
        "https://x.invalid/path",
    ]:
        monkeypatch.setattr(settings, "webapp_url", url)
        with pytest.raises(ValueError, match="configuration"):
            intent_keyboard(parse_intent("ref_39_" + A), "ru")
