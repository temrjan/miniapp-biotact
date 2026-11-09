"""Runs inside FSM event isolation, before logging/database update middlewares."""

import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, Update

from bot.event_entry import EVENT_KEY
from bot.handlers import web_login
from bot.referral_entry import INTENT_KEY

logger = logging.getLogger(__name__)
private_scope: ContextVar[bool] = ContextVar("bot_private_referral", default=False)
_installed = False


def install_log_redaction() -> None:
    global _installed
    if _installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        if (
            private_scope.get()
            or "api.telegram.org/bot" in record.getMessage()
            or "/bot/buyer/telegram" in record.getMessage()
        ):
            record.msg = "Referral entry diagnostic"
            record.args = ()
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return record

    logging.setLogRecordFactory(factory)
    _installed = True


def is_ref_update(event: Update) -> bool:
    text = event.message.text if event.message else None
    return bool(
        text
        and text.split(maxsplit=1)[0].split("@")[0].lower() == "/start"
        and len(text.split(maxsplit=1)) == 2
        and text.split(maxsplit=1)[1].startswith("ref_")
    )


class ReferralEntryMiddleware(BaseMiddleware):
    def __init__(self) -> None:
        install_log_redaction()

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        state: FSMContext | None = data.get("state")
        web = web_login.is_web_update(event)
        private = web or is_ref_update(event)
        if state is not None:
            pending = await state.get_data()
            private = private or INTENT_KEY in pending
            if event.message and event.message.chat.type == "private":
                command = await CommandStart()(event.message, data["bot"])
                if command and not web:
                    # Every namespace retires a pending ref and event intent; its specific route
                    # may install a new one.
                    pending.pop(INTENT_KEY, None)
                    pending.pop(EVENT_KEY, None)
                    await state.set_data(pending)
        data["private_referral_entry"] = private
        scope_token = private_scope.set(private)
        try:
            return await handler(event, data)
        except Exception as error:
            if not private:
                raise
            if web and isinstance(error, web_login.PromptThrottled):
                return True
            if web and isinstance(error, TelegramRetryAfter):
                # A non-prompt web message may also be throttled. Never immediately
                # send the generic error through that same channel.
                message = event.message or (
                    event.callback_query.message if event.callback_query else None
                )
                actor = (
                    event.message.from_user
                    if event.message
                    else (event.callback_query.from_user if event.callback_query else None)
                )
                if (
                    isinstance(message, Message)
                    and actor is not None
                    and not actor.is_bot
                    and web_login.actor_valid(message, actor.id)
                ):
                    await web_login.report_retry_after(actor.id, error.retry_after)
                return True
            if state is not None and not web:
                await state.clear()
            # Consume before the shared global error handler can serialize Update/transport URL.
            logger.error("Referral entry update failed")
            message = event.message or (
                event.callback_query.message if event.callback_query else None
            )
            if isinstance(message, Message):
                try:
                    await data["bot"].send_message(
                        message.chat.id,
                        (
                            "Website sign-in is temporarily unavailable. Return to the originating browser."
                            if web
                            else "Ссылка сейчас недоступна. Нажмите /start, чтобы открыть магазин.\nHavola hozir mavjud emas. Do'konni ochish uchun /start bosing."
                        ),
                        request_timeout=10,
                    )
                except Exception:
                    # A transport timeout may have delivered this fixed message already.
                    pass
            return True
        finally:
            private_scope.reset(scope_token)
