"""Event intent through the registration steps and its welcome (spec event-qr-discount R-33, R-34).

The intent is the scanned promotion id under :data:`EVENT_KEY` in FSM state. Every private
``/start`` retires it (``ReferralEntryMiddleware``); the welcome exit that consumes it clears it.
Texts are the drafts of spec §4.1 and §4.2 (D5).
"""

from datetime import timedelta
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, User, WebAppInfo
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.config import settings
from bot.services.event_promotions import PromotionView, active_grant_promotion

EVENT_KEY = "event_promotion_entry"
TASHKENT = ZoneInfo("Asia/Tashkent")

WELCOME = {
    "ru": (
        "👋 Здравствуйте, {name}! Добро пожаловать в BIOTACT DEUTSCHLAND — пробиотики и "
        "витаминно-минеральные комплексы немецкого качества. 🎁 Для гостей {title} действует "
        "скидка {p}% до {date} — она применится в корзине. Нажмите «Войти», чтобы открыть магазин."
    ),
    "uz": (
        "👋 Assalomu alaykum, {name}! BIOTACT DEUTSCHLAND'ga xush kelibsiz — nemis sifatidagi "
        "probiotiklar va vitamin-mineral komplekslar. 🎁 {title} mehmonlari uchun {date} gacha "
        "{p}% chegirma amal qiladi — u savatchada qo'llaniladi. Do'konni ochish uchun «Kirish» "
        "tugmasini bosing."
    ),
}
BUTTON = {"ru": "Войти", "uz": "Kirish"}


def last_day(promotion: PromotionView) -> str:
    """The last valid Tashkent day of the window, e.g. ``31.12.2026``."""
    final = (promotion.ends_at - timedelta(microseconds=1)).astimezone(TASHKENT)
    return final.strftime("%d.%m.%Y")


def first_step_prefix(promotion: PromotionView) -> str:
    """The §4.1 line above the language prompt for a new user with the event intent."""
    title = escape(promotion.title, quote=False)
    p = promotion.percent
    return f"🎁 Скидка {p}% для гостей {title} / {title} mehmonlari uchun {p}% chegirma"


def intent_of(data: dict[str, Any]) -> int | None:
    """The promotion id of the event intent held in FSM data, if any."""
    value = data.get(EVENT_KEY)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def event_welcome(
    session: AsyncSession, data: dict[str, Any], user: User, lang: str
) -> tuple[str, InlineKeyboardMarkup] | None:
    """The §4.2 welcome iff the event intent is in ``data`` and its grant is active now.

    ``data`` is FSM data read before any ``state.clear()`` on the calling path. ``None`` means
    the exit sends its base welcome and keyboard.
    """
    promotion_id = intent_of(data)
    if promotion_id is None or not settings.event_promotions_enabled:
        return None
    promotion = await active_grant_promotion(session, user.id, promotion_id)
    if promotion is None:
        return None
    lang = "uz" if lang == "uz" else "ru"
    text = WELCOME[lang].format(
        name=escape(user.first_name, quote=False),
        title=escape(promotion.title, quote=False),
        p=promotion.percent,
        date=last_day(promotion),
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=BUTTON[lang], web_app=WebAppInfo(url=settings.webapp_url))]
        ]
    )
    return text, keyboard
