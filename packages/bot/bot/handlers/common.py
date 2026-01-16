"""Common handlers: /start, /help, callbacks."""

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import LanguageCallback
from bot.keyboards.inline import (
    get_language_keyboard,
    get_main_menu_keyboard,
    get_webapp_keyboard,
)
from bot.services.user import UserService


router = Router(name="common")


# =============================================================================
# Localized texts
# =============================================================================

TEXTS = {
    "ru": {
        "welcome": (
            "👋 Привет, {name}!\n\n"
            "Добро пожаловать в <b>BIOTACT DEUTSCHLAND</b> — "
            "пробиотики и витаминно-минеральные комплексы немецкого качества."
        ),
        "help": (
            "<b>❓ Справка</b>\n\n"
            "📱 <b>Как открыть магазин:</b>\n"
            "Нажмите кнопку ниже или используйте голубую кнопку Open\n\n"
            "📦 <b>Доставка:</b>\n"
            "Бесплатная от 250 000 сум\n\n"
            "📞 <b>Поддержка:</b>\n"
            "+998 93 555 17 47"
        ),
        "orders_empty": (
            "📦 <b>Мои заказы</b>\n\n"
            "История заказов пока пуста.\n"
            "Откройте магазин для первого заказа!"
        ),
        "contacts": (
            "<b>📞 Контакты</b>\n\n"
            "📞 +998 93 555 17 47\n"
            "📧 contact_uz@biotact.de\n\n"
            "🕐 9:00 - 21:00 (без выходных)"
        ),
        "language_selected": "✅ Язык изменён на русский",
    },
    "uz": {
        "welcome": (
            "👋 Salom, {name}!\n\n"
            "<b>BIOTACT DEUTSCHLAND</b>ga xush kelibsiz — "
            "nemis sifatidagi probiotiklar va vitamin-mineral komplekslar."
        ),
        "help": (
            "<b>❓ Yordam</b>\n\n"
            "📱 <b>Do'konni qanday ochish:</b>\n"
            "Quyidagi tugmani bosing yoki ko'k Open tugmasini ishlating\n\n"
            "📦 <b>Yetkazib berish:</b>\n"
            "250 000 so'mdan bepul\n\n"
            "📞 <b>Qo'llab-quvvatlash:</b>\n"
            "+998 93 555 17 47"
        ),
        "orders_empty": (
            "📦 <b>Buyurtmalarim</b>\n\n"
            "Buyurtmalar tarixi hali bo'sh.\n"
            "Birinchi buyurtma uchun do'konni oching!"
        ),
        "contacts": (
            "<b>📞 Kontaktlar</b>\n\n"
            "📞 +998 93 555 17 47\n"
            "📧 contact_uz@biotact.de\n\n"
            "🕐 9:00 - 21:00 (dam olish kunlarisiz)"
        ),
        "language_selected": "✅ Til o'zbekchaga o'zgartirildi",
    },
}


def t(lang: str, key: str) -> str:
    """Get localized text."""
    return TEXTS.get(lang, TEXTS["ru"]).get(key, TEXTS["ru"].get(key, key))


# =============================================================================
# Commands
# =============================================================================


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession) -> None:
    """Handle /start command."""
    user_service = UserService(session)
    user, is_new = await user_service.get_or_create(message.from_user)

    if is_new:
        # New user - show language selection
        await message.answer(
            "🌐 Tilni tanlang / Выберите язык:",
            reply_markup=get_language_keyboard(),
        )
    else:
        # Existing user - show welcome
        await message.answer(
            t(user.language_code, "welcome").format(name=message.from_user.first_name),
            reply_markup=get_webapp_keyboard(user.language_code),
        )


@router.message(Command("help"))
async def cmd_help(message: Message, session: AsyncSession) -> None:
    """Handle /help command."""
    user_service = UserService(session)
    user, _ = await user_service.get_or_create(message.from_user)

    await message.answer(
        t(user.language_code, "help"),
        reply_markup=get_webapp_keyboard(user.language_code),
    )


@router.message(Command("language", "lang"))
async def cmd_language(message: Message) -> None:
    """Handle /language command - change language."""
    await message.answer(
        "🌐 Tilni tanlang / Выберите язык:",
        reply_markup=get_language_keyboard(),
    )


# =============================================================================
# Callbacks
# =============================================================================


@router.callback_query(LanguageCallback.filter())
async def callback_language(
    callback: CallbackQuery,
    callback_data: LanguageCallback,
    session: AsyncSession,
) -> None:
    """Handle language selection callback."""
    lang = callback_data.code

    # Update language in database
    user_service = UserService(session)
    user = await user_service.update_language(callback.from_user.id, lang)

    if user:
        # Delete language selection message
        await callback.message.delete()

        # Send welcome in selected language
        await callback.message.answer(
            t(lang, "welcome").format(name=callback.from_user.first_name),
            reply_markup=get_webapp_keyboard(lang),
        )
    else:
        await callback.answer("❌ Error", show_alert=True)

    await callback.answer()


@router.callback_query(F.data == "my_orders")
async def callback_my_orders(
    callback: CallbackQuery,
    session: AsyncSession,
) -> None:
    """Handle my_orders callback."""
    user_service = UserService(session)
    user, _ = await user_service.get_or_create(callback.from_user)

    await callback.message.edit_text(
        t(user.language_code, "orders_empty"),
        reply_markup=get_webapp_keyboard(user.language_code),
    )
    await callback.answer()


@router.callback_query(F.data == "help")
async def callback_help(callback: CallbackQuery, session: AsyncSession) -> None:
    """Handle help callback."""
    user_service = UserService(session)
    user, _ = await user_service.get_or_create(callback.from_user)

    await callback.message.edit_text(
        t(user.language_code, "help"),
        reply_markup=get_main_menu_keyboard(user.language_code),
    )
    await callback.answer()


@router.callback_query(F.data == "contacts")
async def callback_contacts(callback: CallbackQuery, session: AsyncSession) -> None:
    """Handle contacts callback."""
    user_service = UserService(session)
    user, _ = await user_service.get_or_create(callback.from_user)

    await callback.message.edit_text(
        t(user.language_code, "contacts"),
        reply_markup=get_main_menu_keyboard(user.language_code),
    )
    await callback.answer()
