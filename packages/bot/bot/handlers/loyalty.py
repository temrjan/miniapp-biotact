"""Biotact Club loyalty handlers: QR deeplink entry (Stage 4, PR-4.1).

A box QR encodes ``t.me/<bot>?start=qr_<code>``. This router catches the ``qr_`` deeplink
(dispatched away from the admin ``auth_`` deeplink by the ``magic`` prefix filter), collects a
verified phone via a Telegram contact share, then opens the mini app carrying the code in the
URL. While ``loyalty_enabled`` is off the program is dark — a ``qr_`` deeplink falls back to the
normal ``/start``. The code is never logged.
"""

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.config import settings
from bot.handlers.common import cmd_start
from bot.keyboards.inline import (
    get_contact_keyboard,
    get_loyalty_webapp_keyboard,
    get_remove_keyboard,
)
from bot.services.user import UserService

router = Router(name="loyalty")

QR_PREFIX = "qr_"


class LoyaltyActivationState(StatesGroup):
    """States for the QR-deeplink activation flow (distinct from common registration)."""

    waiting_phone = State()


TEXTS = {
    "ru": {
        "share_phone": (
            "📱 Поделитесь номером телефона, чтобы активировать код и получать кэшбэк Biotact Club."
        ),
        "launch": (
            "✅ Код отсканирован! Откройте приложение, чтобы активировать его и получить кэшбэк."
        ),
        "saved": "✅ Спасибо! Номер сохранён.",
    },
    "uz": {
        "share_phone": (
            "📱 Kodni faollashtirish va Biotact Club keshbekini olish uchun "
            "telefon raqamingizni ulashing."
        ),
        "launch": ("✅ Kod skaner qilindi! Uni faollashtirib, keshbek olish uchun ilovani oching."),
        "saved": "✅ Rahmat! Raqam saqlandi.",
    },
}


def _t(lang: str, key: str) -> str:
    """Localized text with a Russian fallback."""
    return TEXTS.get(lang, TEXTS["ru"]).get(key, TEXTS["ru"][key])


async def _send_activation_launch(message: Message, code: str, lang: str) -> None:
    """Open the mini app with the scanned code carried in the URL query."""
    await message.answer(_t(lang, "launch"), reply_markup=get_loyalty_webapp_keyboard(code, lang))


@router.message(CommandStart(deep_link=True, magic=F.args.startswith(QR_PREFIX)))
async def cmd_start_qr(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Handle the ``qr_<code>`` deeplink: verify phone, then launch the mini app."""
    # Dark gate: while the program is off, behave like a normal /start (code ignored).
    if not settings.loyalty_enabled:
        await cmd_start(message, session, state)
        return

    code = (command.args or "").removeprefix(QR_PREFIX)
    if not code:
        await cmd_start(message, session, state)
        return

    user_service = UserService(session)
    user, _ = await user_service.get_or_create(message.from_user)
    lang = user.language_code

    # Already-verified phone → straight to the mini app with the code.
    if user.phone_verified_at is not None:
        await _send_activation_launch(message, code, lang)
        return

    # Otherwise stash the code and ask for a contact (only the bot can get a verified number).
    await state.set_state(LoyaltyActivationState.waiting_phone)
    await state.update_data(loyalty_code=code, lang=lang)
    await message.answer(_t(lang, "share_phone"), reply_markup=get_contact_keyboard(lang))


@router.message(LoyaltyActivationState.waiting_phone, F.contact)
async def process_loyalty_contact(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
) -> None:
    """Store the user's OWN shared contact as a verified phone, then launch the mini app."""
    data = await state.get_data()
    lang = data.get("lang", "ru")

    # Only the sender's own contact (from the request_contact button) is a verified number.
    # A user can also attach an arbitrary contact card whose user_id is None/foreign and whose
    # number is attacker-controlled — reject and re-ask, else someone else's number would be
    # stamped phone_verified_at on this account.
    if message.contact.user_id != message.from_user.id:
        await message.answer(_t(lang, "share_phone"), reply_markup=get_contact_keyboard(lang))
        return

    phone = message.contact.phone_number
    if not phone.startswith("+"):
        phone = f"+{phone}"

    code = data.get("loyalty_code")
    user_service = UserService(session)
    await user_service.set_verified_phone(message.from_user.id, phone)
    await state.clear()

    await message.answer(_t(lang, "saved"), reply_markup=get_remove_keyboard())
    if code:
        await _send_activation_launch(message, code, lang)
