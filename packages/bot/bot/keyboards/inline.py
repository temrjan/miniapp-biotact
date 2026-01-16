"""Inline keyboards for the bot."""

from aiogram.types import InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.core.config import settings
from bot.callbacks import LanguageCallback


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
    
    text = "🛒 Открыть магазин" if lang == "ru" else "🛒 Do'konni ochish"
    builder.button(
        text=text,
        web_app=WebAppInfo(url=settings.webapp_url),
    )
    
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
