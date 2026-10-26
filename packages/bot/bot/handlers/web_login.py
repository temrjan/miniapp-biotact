"""Private website-login namespace; never enters registration or owns auth state."""

import asyncio
import re
import threading
from urllib.parse import urlsplit

import httpx
from aiogram import F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update

from bot.core.config import settings

router = Router(name="web-login")
UNAVAILABLE = (
    "Website sign-in is temporarily unavailable. Return to the originating browser and try again."
)
HANDLE = r"[0-9a-f]{32}"
_slots = threading.BoundedSemaphore(4)
STATES = {"pending", "approved", "declined", "expired", "cancelled", "completed"}


class PromptThrottled(Exception):
    """The API refused another presentation; do not use Telegram as a fallback."""


def is_web_update(event: Update) -> bool:
    if event.callback_query and (event.callback_query.data or "").startswith("web:"):
        return True
    text = event.message.text if event.message else None
    if not text:
        return False
    pieces = text.split(maxsplit=1)
    return (
        len(pieces) == 2
        and pieces[0].split("@")[0].lower() == "/start"
        and pieces[1].startswith("web_")
    )


async def bridge(path: str, payload: dict[str, object]) -> dict[str, object] | None:
    if not settings.buyer_telegram_auth_enabled:
        return None
    origin = urlsplit(settings.buyer_telegram_api_url)
    if (
        origin.scheme != "https"
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.query
        or origin.fragment
    ):
        return None
    try:
        async with httpx.AsyncClient(
            timeout=10, verify=True, follow_redirects=False, trust_env=False
        ) as client:
            response = await client.post(
                settings.buyer_telegram_api_url + "/" + path,
                json=payload,
                headers={"X-Bot-Token": settings.bot_token.get_secret_value()},
            )
            if response.status_code == 429 and path == "present":
                raise PromptThrottled
            if response.status_code != 200:
                return None
            data = response.json()
            if path == "retry-after":
                return {} if isinstance(data, dict) and not data else None
            if not isinstance(data, dict) or data.get("state") not in STATES:
                return None
            return data
    except PromptThrottled:
        raise
    except Exception:
        return None


async def report_retry_after(actor: int, seconds: int) -> None:
    await bridge("retry-after", {"telegram_id": actor, "retry_after": seconds})


def actor_valid(message: Message, actor_id: int) -> bool:
    return (
        message.chat.type == "private"
        and message.chat.id == actor_id
        and 0 < actor_id <= 9223372036854775807
    )


@router.message(CommandStart(deep_link=True), F.text.regexp(r"^/start(?:@\w+)?\s+web_"))
async def start(message: Message, command: CommandObject) -> None:
    actor = message.from_user
    handle = (command.args or "")[4:]
    if (
        actor is None
        or actor.is_bot
        or not actor_valid(message, actor.id)
        or not re.fullmatch(HANDLE, handle)
    ):
        await message.bot.send_message(
            message.chat.id, UNAVAILABLE, parse_mode=None, request_timeout=10
        )
        return
    data = await bridge("present", {"request_id": handle, "telegram_id": actor.id})
    if data is None:
        await message.bot.send_message(
            message.chat.id, UNAVAILABLE, parse_mode=None, request_timeout=10
        )
        return
    prompt = data.get("prompt")
    if prompt is None:
        await message.bot.send_message(
            message.chat.id,
            f"Website sign-in: {data['state']}. Return to the originating browser.",
            parse_mode=None,
            request_timeout=10,
        )
        return
    fields = ("site", "created_at", "expires_at", "comparison", "confirm_data", "decline_data")
    if (
        not isinstance(prompt, dict)
        or not all(isinstance(prompt.get(key), str) for key in fields)
        or prompt["confirm_data"] != f"web:{handle}:confirm"
        or prompt["decline_data"] != f"web:{handle}:decline"
    ):
        await message.bot.send_message(
            message.chat.id, UNAVAILABLE, parse_mode=None, request_timeout=10
        )
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Confirm", callback_data=prompt["confirm_data"]),
                InlineKeyboardButton(text="Decline", callback_data=prompt["decline_data"]),
            ]
        ]
    )
    if not settings.buyer_telegram_auth_enabled or not _slots.acquire(blocking=False):
        await message.bot.send_message(
            message.chat.id, UNAVAILABLE, parse_mode=None, request_timeout=10
        )
        return

    async def send() -> None:
        try:
            await message.bot.send_message(
                message.chat.id,
                f"Website sign-in: {prompt['site']}\nRequested: {prompt['created_at']}\nExpires: {prompt['expires_at']}\nComparison: {prompt['comparison']}\nConfirm only a request you initiated. Return to the originating browser.\nOpening Telegram does not sign in this device's browser.",
                parse_mode=None,
                reply_markup=keyboard,
                request_timeout=10,
            )
        except TelegramRetryAfter as error:
            await report_retry_after(actor.id, error.retry_after)

    # Cancellation retains ownership through the bounded send and any throttle report.
    worker = asyncio.create_task(send())
    try:
        await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()
        raise
    finally:
        _slots.release()


@router.callback_query(F.data.startswith("web:"))
async def decision(query: CallbackQuery) -> None:
    result = UNAVAILABLE
    try:
        message = query.message
        match = re.fullmatch(rf"web:({HANDLE}):(confirm|decline)", query.data or "")
        if (
            isinstance(message, Message)
            and not query.from_user.is_bot
            and actor_valid(message, query.from_user.id)
            and match
        ):
            data = await bridge(
                "decision",
                {"request_id": match[1], "telegram_id": query.from_user.id, "decision": match[2]},
            )
            if data is not None:
                result = f"Website sign-in: {data['state']}. Return to the originating browser."
                try:
                    await message.bot.edit_message_reply_markup(
                        chat_id=message.chat.id,
                        message_id=message.message_id,
                        reply_markup=None,
                        request_timeout=10,
                    )
                except Exception:
                    pass
    finally:
        try:
            await query.bot.answer_callback_query(
                query.id, result, show_alert=True, request_timeout=10
            )
        except Exception:
            pass
