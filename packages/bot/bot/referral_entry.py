"""Bounded unconfirmed referral intent, held only by the registration FSM."""

import base64
import re
from urllib.parse import urlencode, urlsplit, urlunsplit

from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from bot.core.config import settings
from bot.keyboards.inline import get_webapp_keyboard

INTENT_KEY = "employee_referral_entry"


def product_id(value: str) -> bool:
    return bool(re.fullmatch(r"[1-9][0-9]{0,18}", value)) and int(value) <= 9223372036854775807


def parse_intent(args: str) -> dict[str, str]:
    hint = args.removeprefix("ref_").split("_", 1)[0]
    hint = hint if product_id(hint) else ""
    intent = {"product": hint, "token": ""}
    if len(args) < 28 or args[-23] != "_":
        return intent
    product, token = args[4:-23], args[-22:]
    if not args.startswith("ref_") or not product_id(product):
        return intent
    if not re.fullmatch(r"[A-Za-z0-9_-]{22}", token):
        return intent
    decoded = base64.urlsafe_b64decode(token + "==")
    if len(decoded) == 16 and base64.urlsafe_b64encode(decoded).decode().rstrip("=") == token:
        return {"product": product, "token": token}
    return intent


def intent_keyboard(intent: dict[str, str], lang: str) -> InlineKeyboardMarkup:
    origin = urlsplit(settings.webapp_url)
    if (
        origin.scheme != "https"
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.query
        or origin.fragment
        or origin.path not in {"", "/"}
    ):
        raise ValueError("Referral entry configuration unavailable")
    product = intent.get("product", "")
    path = f"/product/{product}" if product and product_id(product) else "/"
    url = urlunsplit(
        (origin.scheme, origin.netloc, path, urlencode({"ref": intent.get("token", "")}), "")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Mahsulotni ochish" if lang == "uz" else "Открыть товар",
                    web_app=WebAppInfo(url=url),
                )
            ]
        ]
    )


async def launch_keyboard(state: FSMContext, lang: str) -> InlineKeyboardMarkup:
    if not settings.employee_referral_entry_enabled:
        return get_webapp_keyboard(lang)
    data = await state.get_data()
    intent = data.get(INTENT_KEY)
    if settings.employee_referral_entry_enabled and isinstance(intent, dict):
        return intent_keyboard(intent, lang)
    return get_webapp_keyboard(lang)
