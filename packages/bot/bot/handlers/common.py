"""Common handlers: /start, /help, callbacks, AI assistant, Order Flow."""

import asyncio

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import LanguageCallback
from bot.keyboards.inline import (
    get_language_keyboard,
    get_main_menu_keyboard,
    get_webapp_keyboard,
    get_back_keyboard,
    get_shop_or_order_keyboard,
    get_confirm_name_keyboard,
    get_confirm_phone_keyboard,
    get_cancel_order_keyboard,
    get_contact_keyboard,
    get_skip_phone_keyboard,
    get_remove_keyboard,
)
from bot.services.user import UserService
from bot.services.crm import sync_customer_to_crm, sync_customer_full


router = Router(name="common")


# =============================================================================
# FSM States
# =============================================================================

class RegistrationState(StatesGroup):
    """States for new user registration."""
    waiting_phone = State()


class AIAssistantState(StatesGroup):
    """States for AI assistant conversation."""
    chatting = State()


class OrderState(StatesGroup):
    """States for order flow with pre-filled data."""
    confirm_name = State()
    enter_name = State()
    confirm_phone = State()
    enter_phone = State()
    enter_details = State()


# =============================================================================
# Purchase Triggers
# =============================================================================

PURCHASE_TRIGGERS = ["магазин", "купить", "заказ", "заявк", "оформ", "приобрести", "где взять"]
PURCHASE_TRIGGERS_UZ = ["sotib olish", "buyurtma", "ariza", "qayerda", "magazin"]


def has_purchase_trigger(text: str) -> bool:
    """Check if AI response contains purchase trigger words."""
    text_lower = text.lower()
    return any(trigger in text_lower for trigger in PURCHASE_TRIGGERS + PURCHASE_TRIGGERS_UZ)


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
        "ai_welcome": (
            "🤖 <b>AI Ассистент Biotact</b>\n\n"
            "Я помогу подобрать продукт для здоровья и красоты.\n\n"
            "Расскажите, что вас беспокоит?"
        ),
        "order_sent": "✅ Заявка принята!\n\n📞 Ваш номер: {phone}\n\nМенеджер свяжется с вами в ближайшее время.",
        # Order Flow texts
        "confirm_name": "👤 Ваше имя: <b>{name}</b>\n\nВсё верно?",
        "enter_name": "👤 Введите ваше имя:",
        "confirm_phone": "📱 Ваш номер: <b>{phone}</b>\n\nВсё верно?",
        "enter_phone": "📱 Введите ваш номер телефона:",
        "enter_details": "📝 Опишите, какой продукт вас интересует или что беспокоит:\n\n<i>(можно пропустить, отправив любой символ)</i>",
        "order_cancelled": "❌ Заявка отменена.\n\nМожете продолжить консультацию или открыть магазин.",
        "invalid_phone": "❌ Неверный формат номера.\n\nВведите номер в формате: +998 XX XXX XX XX",
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
        "ai_welcome": (
            "🤖 <b>Biotact AI Yordamchi</b>\n\n"
            "Salomatlik va go'zallik uchun mahsulot tanlashda yordam beraman.\n\n"
            "Sizni nima bezovta qilayotganini ayting?"
        ),
        "order_sent": "✅ Ariza qabul qilindi!\n\n📞 Raqamingiz: {phone}\n\nMenejer tez orada siz bilan bog'lanadi.",
        # Order Flow texts
        "confirm_name": "👤 Ismingiz: <b>{name}</b>\n\nTo'g'rimi?",
        "enter_name": "👤 Ismingizni kiriting:",
        "confirm_phone": "📱 Raqamingiz: <b>{phone}</b>\n\nTo'g'rimi?",
        "enter_phone": "📱 Telefon raqamingizni kiriting:",
        "enter_details": "📝 Qaysi mahsulot sizni qiziqtirayotganini yoki nima bezovta qilayotganini yozing:\n\n<i>(o'tkazib yuborish uchun istalgan belgi yuboring)</i>",
        "order_cancelled": "❌ Ariza bekor qilindi.\n\nMaslahatni davom ettirishingiz yoki do'konni ochishingiz mumkin.",
        "invalid_phone": "❌ Noto'g'ri raqam formati.\n\nRaqamni kiriting: +998 XX XXX XX XX",
    },
}


def t(lang: str, key: str) -> str:
    """Get localized text."""
    return TEXTS.get(lang, TEXTS["ru"]).get(key, TEXTS["ru"].get(key, key))


# =============================================================================
# Commands
# =============================================================================


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, state: FSMContext) -> None:
    """Handle /start command."""
    await state.clear()
    user_service = UserService(session)
    user, is_new = await user_service.get_or_create(message.from_user)
    # Sync to CRM (fire and forget)
    asyncio.create_task(sync_customer_to_crm(message.from_user))

    if is_new:
        # New user - show language selection first
        await message.answer(
            "🌐 Tilni tanlang / Выберите язык:",
            reply_markup=get_language_keyboard(),
        )
    elif not user.phone:
        # Existing user without phone - ask for phone
        lang = user.language_code
        await state.set_state(RegistrationState.waiting_phone)
        await state.update_data(lang=lang)

        phone_text = {
            "ru": "📱 Поделитесь номером телефона для связи с менеджером.\n\nЭто поможет нам быстрее обработать ваш заказ.",
            "uz": "📱 Menejer bilan bog'lanish uchun telefon raqamingizni ulashing.\n\nBu buyurtmangizni tezroq qayta ishlashga yordam beradi.",
        }
        await message.answer(
            phone_text.get(lang, phone_text["ru"]),
            reply_markup=get_contact_keyboard(lang),
        )
        await message.answer(
            "👇" if lang == "ru" else "👇",
            reply_markup=get_skip_phone_keyboard(lang),
        )
    else:
        # Existing user with phone - show welcome
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
    """Handle /language command."""
    await message.answer(
        "🌐 Tilni tanlang / Выберите язык:",
        reply_markup=get_language_keyboard(),
    )


# =============================================================================
# Language & Menu Callbacks
# =============================================================================


@router.callback_query(LanguageCallback.filter())
async def callback_language(
    callback: CallbackQuery,
    callback_data: LanguageCallback,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Handle language selection callback."""
    lang = callback_data.code
    user_service = UserService(session)
    user = await user_service.update_language(callback.from_user.id, lang)

    if user:
        await callback.message.delete()

        # Check if user already has phone
        if user.phone:
            # Already has phone, show welcome
            await callback.message.answer(
                t(lang, "welcome").format(name=callback.from_user.first_name),
                reply_markup=get_webapp_keyboard(lang),
            )
        else:
            # Ask for phone number
            await state.set_state(RegistrationState.waiting_phone)
            await state.update_data(lang=lang)

            phone_text = {
                "ru": "📱 Поделитесь номером телефона для связи с менеджером.\n\nЭто поможет нам быстрее обработать ваш заказ.",
                "uz": "📱 Menejer bilan bog'lanish uchun telefon raqamingizni ulashing.\n\nBu buyurtmangizni tezroq qayta ishlashga yordam beradi.",
            }
            await callback.message.answer(
                phone_text.get(lang, phone_text["ru"]),
                reply_markup=get_contact_keyboard(lang),
            )
            await callback.message.answer(
                "👇" if lang == "ru" else "👇",
                reply_markup=get_skip_phone_keyboard(lang),
            )
    else:
        await callback.answer("❌ Error", show_alert=True)

    await callback.answer()


# =============================================================================
# Phone Registration Handlers
# =============================================================================


@router.message(RegistrationState.waiting_phone, F.contact)
async def handle_contact_received(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Handle when user shares their contact."""
    phone = message.contact.phone_number
    if not phone.startswith("+"):
        phone = f"+{phone}"

    user_service = UserService(session)
    user = await user_service.update_phone(message.from_user.id, phone)

    await state.clear()

    # Get language from state or user
    data = await state.get_data()
    lang = data.get("lang", user.language_code if user else "ru")

    # Sync full user data to CRM (fire and forget)
    asyncio.create_task(sync_customer_full(message.from_user, phone))

    # Show success and welcome
    success_text = {
        "ru": "✅ Спасибо! Номер сохранён.",
        "uz": "✅ Rahmat! Raqam saqlandi.",
    }
    await message.answer(
        success_text.get(lang, success_text["ru"]),
        reply_markup=get_remove_keyboard(),
    )
    await message.answer(
        t(lang, "welcome").format(name=message.from_user.first_name),
        reply_markup=get_webapp_keyboard(lang),
    )


@router.callback_query(F.data == "skip_phone")
async def callback_skip_phone(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Handle when user skips phone sharing via inline button."""
    user_service = UserService(session)
    user, _ = await user_service.get_or_create(callback.from_user)

    # Get language before clearing state
    data = await state.get_data()
    lang = data.get("lang", user.language_code if user else "ru")

    await state.clear()

    # Sync user to CRM (fire and forget)
    asyncio.create_task(sync_customer_to_crm(callback.from_user))

    await callback.message.delete()
    await callback.message.answer(
        t(lang, "welcome").format(name=callback.from_user.first_name),
        reply_markup=get_remove_keyboard(),
    )
    await callback.message.answer(
        t(lang, "welcome").format(name=callback.from_user.first_name),
        reply_markup=get_webapp_keyboard(lang),
    )
    await callback.answer()


@router.callback_query(F.data == "ai_assistant")
async def callback_ai_assistant(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Handle AI assistant button callback."""
    user_service = UserService(session)
    user, _ = await user_service.get_or_create(callback.from_user)

    await state.set_state(AIAssistantState.chatting)
    await state.update_data(ai_history=[])

    await callback.message.edit_text(
        t(user.language_code, "ai_welcome"),
        reply_markup=get_back_keyboard(user.language_code),
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_menu")
async def callback_back_to_menu(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Handle back to menu callback."""
    await state.clear()
    user_service = UserService(session)
    user, _ = await user_service.get_or_create(callback.from_user)
    
    await callback.message.edit_text(
        t(user.language_code, "welcome").format(name=callback.from_user.first_name),
        reply_markup=get_webapp_keyboard(user.language_code),
    )
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


# =============================================================================
# Order Flow Callbacks
# =============================================================================


@router.callback_query(F.data == "continue_ai")
async def callback_continue_ai(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Continue AI consultation after purchase suggestion."""
    user_service = UserService(session)
    user, _ = await user_service.get_or_create(callback.from_user)
    
    await state.set_state(AIAssistantState.chatting)
    await callback.message.edit_text(
        "Продолжаем консультацию. Задайте следующий вопрос." if user.language_code == "ru" 
        else "Maslahatni davom etamiz. Keyingi savolingizni bering.",
        reply_markup=get_back_keyboard(user.language_code),
    )
    await callback.answer()


@router.callback_query(F.data == "start_order")
async def callback_start_order(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Start order flow - ask to confirm name."""
    user_service = UserService(session)
    user, _ = await user_service.get_or_create(callback.from_user)
    
    # Get data from state (AI history for order context)
    data = await state.get_data()
    
    # Pre-fill name from Telegram
    name = callback.from_user.full_name
    
    await state.set_state(OrderState.confirm_name)
    await state.update_data(
        order_name=name,
        ai_history=data.get("ai_history", []),
    )
    
    await callback.message.edit_text(
        t(user.language_code, "confirm_name").format(name=name),
        reply_markup=get_confirm_name_keyboard(user.language_code),
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_name_yes", OrderState.confirm_name)
async def callback_confirm_name_yes(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Name confirmed - proceed to phone."""
    user_service = UserService(session)
    user, _ = await user_service.get_or_create(callback.from_user)
    
    # Check if user has saved phone
    phone = user.phone
    
    if phone:
        # Pre-fill phone from DB
        await state.set_state(OrderState.confirm_phone)
        await state.update_data(order_phone=phone)
        
        await callback.message.edit_text(
            t(user.language_code, "confirm_phone").format(phone=phone),
            reply_markup=get_confirm_phone_keyboard(user.language_code),
        )
    else:
        # Ask to enter phone
        await state.set_state(OrderState.enter_phone)
        await callback.message.edit_text(
            t(user.language_code, "enter_phone"),
            reply_markup=get_cancel_order_keyboard(user.language_code),
        )
    
    await callback.answer()


@router.callback_query(F.data == "confirm_name_change", OrderState.confirm_name)
async def callback_confirm_name_change(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """User wants to change name."""
    user_service = UserService(session)
    user, _ = await user_service.get_or_create(callback.from_user)
    
    await state.set_state(OrderState.enter_name)
    await callback.message.edit_text(
        t(user.language_code, "enter_name"),
        reply_markup=get_cancel_order_keyboard(user.language_code),
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_phone_yes", OrderState.confirm_phone)
async def callback_confirm_phone_yes(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Phone confirmed - proceed to details."""
    user_service = UserService(session)
    user, _ = await user_service.get_or_create(callback.from_user)
    
    await state.set_state(OrderState.enter_details)
    await callback.message.edit_text(
        t(user.language_code, "enter_details"),
        reply_markup=get_cancel_order_keyboard(user.language_code),
    )
    await callback.answer()


@router.callback_query(F.data == "confirm_phone_change", OrderState.confirm_phone)
async def callback_confirm_phone_change(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """User wants to change phone."""
    user_service = UserService(session)
    user, _ = await user_service.get_or_create(callback.from_user)
    
    await state.set_state(OrderState.enter_phone)
    await callback.message.edit_text(
        t(user.language_code, "enter_phone"),
        reply_markup=get_cancel_order_keyboard(user.language_code),
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_order")
async def callback_cancel_order(
    callback: CallbackQuery,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Cancel order flow."""
    user_service = UserService(session)
    user, _ = await user_service.get_or_create(callback.from_user)
    
    await state.clear()
    await callback.message.edit_text(
        t(user.language_code, "order_cancelled"),
        reply_markup=get_webapp_keyboard(user.language_code),
    )
    await callback.answer()


# =============================================================================
# Order Flow Message Handlers
# =============================================================================


@router.message(OrderState.enter_name, F.text)
async def handle_enter_name(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Handle name input."""
    user_service = UserService(session)
    user, _ = await user_service.get_or_create(message.from_user)
    
    name = message.text.strip()
    await state.update_data(order_name=name)
    
    # Check if user has saved phone
    phone = user.phone
    
    if phone:
        await state.set_state(OrderState.confirm_phone)
        await state.update_data(order_phone=phone)
        
        await message.answer(
            t(user.language_code, "confirm_phone").format(phone=phone),
            reply_markup=get_confirm_phone_keyboard(user.language_code),
        )
    else:
        await state.set_state(OrderState.enter_phone)
        await message.answer(
            t(user.language_code, "enter_phone"),
            reply_markup=get_cancel_order_keyboard(user.language_code),
        )


@router.message(OrderState.enter_phone, F.text)
async def handle_enter_phone(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Handle phone input."""
    from bot.services.notification import extract_phone
    
    user_service = UserService(session)
    user, _ = await user_service.get_or_create(message.from_user)
    
    phone = extract_phone(message.text)
    
    if not phone:
        await message.answer(
            t(user.language_code, "invalid_phone"),
            reply_markup=get_cancel_order_keyboard(user.language_code),
        )
        return
    
    # Save phone to DB for future orders
    await user_service.update_phone(message.from_user.id, phone)
    
    await state.update_data(order_phone=phone)
    await state.set_state(OrderState.enter_details)
    
    await message.answer(
        t(user.language_code, "enter_details"),
        reply_markup=get_cancel_order_keyboard(user.language_code),
    )


@router.message(OrderState.enter_details, F.text)
async def handle_enter_details(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Handle details input - complete order."""
    from bot.services.notification import send_order_to_sales
    
    user_service = UserService(session)
    user, _ = await user_service.get_or_create(message.from_user)
    
    data = await state.get_data()
    name = data.get("order_name", message.from_user.full_name)
    phone = data.get("order_phone", "")
    details = message.text.strip()
    
    # Build conversation summary from AI history
    ai_history = data.get("ai_history", [])
    summary_parts = []
    for msg in ai_history[-6:]:
        if msg["role"] == "user":
            summary_parts.append(f"• {msg['text']}")
    
    if details and details != "-":
        summary_parts.append(f"• Комментарий: {details}")
    
    summary = "\n".join(summary_parts) if summary_parts else "Заявка через AI ассистента"
    
    # Send order to sales
    order_sent = await send_order_to_sales(
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=name,
        phone=phone,
        conversation_summary=summary,
    )
    
    await state.clear()
    
    if order_sent:
        await message.answer(
            t(user.language_code, "order_sent").format(phone=phone),
            reply_markup=get_webapp_keyboard(user.language_code),
        )
    else:
        await message.answer(
            "❌ Ошибка отправки заявки. Попробуйте позже." if user.language_code == "ru"
            else "❌ Ariza yuborishda xatolik. Keyinroq urinib ko'ring.",
            reply_markup=get_webapp_keyboard(user.language_code),
        )


# =============================================================================
# AI Assistant Message Handler
# =============================================================================


@router.message(AIAssistantState.chatting, F.text)
async def handle_ai_message(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Handle messages in AI assistant mode."""
    from bot.services.askbiotact import ask_biotact
    from bot.services.notification import extract_phone, send_order_to_sales
    
    user_service = UserService(session)
    user, _ = await user_service.get_or_create(message.from_user)
    
    # Get state data for conversation history
    data = await state.get_data()
    ai_history = data.get("ai_history", [])
    
    # Add user message to history
    ai_history.append({"role": "user", "text": message.text[:500]})
    
    await message.bot.send_chat_action(message.chat.id, "typing")
    
    # Get AI response
    response = await ask_biotact(message.from_user.id, message.text)
    
    # Add AI response to history
    ai_history.append({"role": "assistant", "text": response[:500]})
    
    # Keep only last 10 messages
    ai_history = ai_history[-10:]
    await state.update_data(ai_history=ai_history)
    
    # Check for phone number in user message (old flow - still works)
    phone = extract_phone(message.text)
    if phone:
        # Build conversation summary
        summary_parts = []
        for msg in ai_history[:-2]:
            if msg["role"] == "user":
                summary_parts.append(f"• {msg['text']}")
        summary = "\n".join(summary_parts[-5:]) if summary_parts else "Консультация по продуктам"
        
        # Save phone for future
        await user_service.update_phone(message.from_user.id, phone)
        
        # Send order to sales group
        order_sent = await send_order_to_sales(
            user_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name,
            phone=phone,
            conversation_summary=summary,
        )
        
        if order_sent:
            await message.answer(
                t(user.language_code, "order_sent").format(phone=phone),
                reply_markup=get_back_keyboard(user.language_code),
            )
            return
    
    # Check for purchase trigger in AI response
    if has_purchase_trigger(response):
        await message.answer(response, reply_markup=get_shop_or_order_keyboard(user.language_code))
    else:
        await message.answer(response, reply_markup=get_back_keyboard(user.language_code))


@router.message(F.text)
async def handle_message(message: Message) -> None:
    """Handle regular text messages - show menu hint."""
    if message.text and message.text.startswith("/"):
        return
    
    # For messages outside AI mode, suggest using the button
    await message.answer(
        "Откройте магазин для заказа 🛒\n"
        "Do'konni ochish uchun tugmani bosing 🛒",
        reply_markup=get_webapp_keyboard("ru"),
    )
