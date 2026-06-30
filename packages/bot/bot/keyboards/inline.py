"""Inline and Reply keyboards for the bot."""

from urllib.parse import quote

from aiogram.types import (
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from bot.callbacks import LanguageCallback
from bot.core.config import settings

# =============================================================================
# Reply Keyboards (for contact request)
# =============================================================================


def get_contact_keyboard(lang: str = "ru") -> ReplyKeyboardMarkup:
    """Get reply keyboard to request user's phone number (contact sharing)."""
    texts = {
        "ru": {"share": "📱 Поделиться номером"},
        "uz": {"share": "📱 Raqamni ulashish"},
    }
    t = texts.get(lang, texts["ru"])

    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text=t["share"], request_contact=True))

    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def get_skip_phone_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    """Get inline keyboard with skip button for phone step."""
    text = "⏭️ Пропустить" if lang == "ru" else "⏭️ O'tkazib yuborish"
    builder = InlineKeyboardBuilder()
    builder.button(text=text, callback_data="skip_phone")
    return builder.as_markup()


def get_remove_keyboard() -> ReplyKeyboardRemove:
    """Remove reply keyboard."""
    return ReplyKeyboardRemove()


def get_language_keyboard() -> InlineKeyboardMarkup:
    """Get language selection keyboard."""
    builder = InlineKeyboardBuilder()

    builder.button(
        text="🇷🇺 Русский",
        callback_data=LanguageCallback(code="ru"),
    )
    builder.button(
        text="🇺🇿 O'zbekcha",
        callback_data=LanguageCallback(code="uz"),
    )

    builder.adjust(2)
    return builder.as_markup()


def get_webapp_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    """Get keyboard with Mini App button."""
    builder = InlineKeyboardBuilder()

    # Shop button
    shop_text = "🛒 Открыть магазин" if lang == "ru" else "🛒 Do'konni ochish"
    builder.button(
        text=shop_text,
        web_app=WebAppInfo(url=settings.webapp_url),
    )

    # AI Assistant disabled - LLM issues
    # ai_text = "🤖 AI Ассистент" if lang == "ru" else "🤖 AI Yordamchi"
    # builder.button(text=ai_text, callback_data="ai_assistant")

    builder.adjust(1)
    return builder.as_markup()


def get_loyalty_webapp_keyboard(code: str, lang: str = "ru") -> InlineKeyboardMarkup:
    """Mini App launcher carrying a scanned QR code via ``?code=`` (Biotact Club activation).

    The bot opens the web app through this inline button; the code rides in the URL query
    (``start_param`` is not delivered for web_app buttons). ``quote`` keeps the URL valid.
    """
    text = "🎁 Получить кэшбэк" if lang == "ru" else "🎁 Keshbekni olish"
    builder = InlineKeyboardBuilder()
    builder.button(
        text=text,
        web_app=WebAppInfo(url=f"{settings.webapp_url}?code={quote(code, safe='')}"),
    )
    builder.adjust(1)
    return builder.as_markup()


def get_main_menu_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    """Get main menu inline keyboard."""
    builder = InlineKeyboardBuilder()

    # Localized texts
    texts = {
        "ru": {
            "shop": "🛒 Открыть магазин",
            "help": "❓ Помощь",
            "contacts": "📞 Контакты",
        },
        "uz": {
            "shop": "🛒 Do'konni ochish",
            "help": "❓ Yordam",
            "contacts": "📞 Kontaktlar",
        },
    }
    t = texts.get(lang, texts["ru"])

    # Mini App button
    builder.button(
        text=t["shop"],
        web_app=WebAppInfo(url=settings.webapp_url),
    )

    # AI Assistant disabled - LLM issues
    # builder.button(text=t["ai"], callback_data="ai_assistant")

    # Other buttons
    builder.button(text=t["help"], callback_data="help")
    builder.button(text=t["contacts"], callback_data="contacts")

    builder.adjust(1, 2)
    return builder.as_markup()


def get_order_keyboard(order_id: int, lang: str = "ru") -> InlineKeyboardMarkup:
    """Get keyboard for order details."""
    builder = InlineKeyboardBuilder()

    texts = {
        "ru": {"repeat": "🔄 Повторить заказ", "back": "◀️ Назад"},
        "uz": {"repeat": "🔄 Takrorlash", "back": "◀️ Orqaga"},
    }
    t = texts.get(lang, texts["ru"])

    builder.button(text=t["repeat"], callback_data=f"repeat_order:{order_id}")
    builder.button(text=t["back"], callback_data="my_orders")

    builder.adjust(1)
    return builder.as_markup()


def get_back_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    """Get keyboard with back button."""
    builder = InlineKeyboardBuilder()

    text = "◀️ Назад в меню" if lang == "ru" else "◀️ Menyuga qaytish"
    builder.button(text=text, callback_data="back_to_menu")

    return builder.as_markup()


# =============================================================================
# Order Flow Keyboards
# =============================================================================


def get_shop_or_order_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    """Get keyboard with shop and order buttons (shown after AI suggests purchase)."""
    builder = InlineKeyboardBuilder()

    texts = {
        "ru": {
            "shop": "🛍️ Открыть магазин",
            "order": "📝 Оставить заявку",
            "back": "◀️ Продолжить консультацию",
        },
        "uz": {
            "shop": "🛍️ Do'konni ochish",
            "order": "📝 Ariza qoldirish",
            "back": "◀️ Maslahatni davom ettirish",
        },
    }
    t = texts.get(lang, texts["ru"])

    builder.button(
        text=t["shop"],
        web_app=WebAppInfo(url=settings.webapp_url),
    )
    builder.button(text=t["order"], callback_data="start_order")
    builder.button(text=t["back"], callback_data="continue_ai")

    builder.adjust(1)
    return builder.as_markup()


def get_confirm_name_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    """Get keyboard to confirm pre-filled name."""
    builder = InlineKeyboardBuilder()

    texts = {
        "ru": {"yes": "✅ Да, верно", "change": "✏️ Изменить"},
        "uz": {"yes": "✅ Ha, to'g'ri", "change": "✏️ O'zgartirish"},
    }
    t = texts.get(lang, texts["ru"])

    builder.button(text=t["yes"], callback_data="confirm_name_yes")
    builder.button(text=t["change"], callback_data="confirm_name_change")

    builder.adjust(2)
    return builder.as_markup()


def get_confirm_phone_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    """Get keyboard to confirm pre-filled phone."""
    builder = InlineKeyboardBuilder()

    texts = {
        "ru": {"yes": "✅ Да, верно", "change": "✏️ Изменить"},
        "uz": {"yes": "✅ Ha, to'g'ri", "change": "✏️ O'zgartirish"},
    }
    t = texts.get(lang, texts["ru"])

    builder.button(text=t["yes"], callback_data="confirm_phone_yes")
    builder.button(text=t["change"], callback_data="confirm_phone_change")

    builder.adjust(2)
    return builder.as_markup()


def get_cancel_order_keyboard(lang: str = "ru") -> InlineKeyboardMarkup:
    """Get keyboard with cancel button during order flow."""
    builder = InlineKeyboardBuilder()

    text = "❌ Отменить заявку" if lang == "ru" else "❌ Arizani bekor qilish"
    builder.button(text=text, callback_data="cancel_order")

    return builder.as_markup()
