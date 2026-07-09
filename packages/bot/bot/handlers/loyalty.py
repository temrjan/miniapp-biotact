"""Biotact Club loyalty handlers: QR deeplink + manual code entry (Stage 4).

A box QR encodes ``t.me/<bot>?start=qr_<code>``. This router catches the ``qr_`` deeplink
(dispatched away from the admin ``auth_`` deeplink by the ``magic`` prefix filter), collects a
verified phone via a Telegram contact share, then opens the mini app carrying the code in the URL.

If the camera cannot read the QR, the same code (printed under it) can be typed by hand: a
code-shaped text message takes the identical path (:func:`_start_activation`). Both entries funnel
into the mini app, where consent + activation happen. While ``loyalty_enabled`` is off the program
is dark — a ``qr_`` deeplink falls back to the normal ``/start`` and a typed code falls through to
ordinary handlers. The code is never logged.
"""

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart, StateFilter
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

# Crockford base32 code contract — mirror of ``src/loyalty/codes.py`` (separate package, cannot
# import). The alphabet excludes I/L/O/U so a hand-typed code is unambiguous; on input we fold the
# confusables back (O->0, I/L->1). U is not in the alphabet and is not remapped → a code carrying U
# is simply not code-shaped.
_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_LENGTH = 12
_CONFUSABLES = str.maketrans({"O": "0", "I": "1", "L": "1"})


class LoyaltyActivationState(StatesGroup):
    """States for the activation flow (distinct from common registration)."""

    waiting_phone = State()


TEXTS = {
    "ru": {
        "share_phone": (
            "📱 Поделитесь номером телефона, чтобы активировать код и получать кэшбэк Biotact Club."
        ),
        "launch": ("✅ Код принят! Откройте приложение, чтобы активировать его и получить кэшбэк."),
        "saved": "✅ Спасибо! Номер сохранён.",
    },
    "uz": {
        "share_phone": (
            "📱 Kodni faollashtirish va Biotact Club keshbekini olish uchun "
            "telefon raqamingizni ulashing."
        ),
        "launch": ("✅ Kod qabul qilindi! Uni faollashtirib, keshbek olish uchun ilovani oching."),
        "saved": "✅ Rahmat! Raqam saqlandi.",
    },
}


def _t(lang: str, key: str) -> str:
    """Localized text with a Russian fallback."""
    return TEXTS.get(lang, TEXTS["ru"]).get(key, TEXTS["ru"][key])


async def _send_activation_launch(message: Message, code: str, lang: str) -> None:
    """Open the mini app with the code carried in the URL query."""
    await message.answer(_t(lang, "launch"), reply_markup=get_loyalty_webapp_keyboard(code, lang))


async def _start_activation(
    message: Message, session: AsyncSession, state: FSMContext, code: str
) -> None:
    """Shared tail for both entries (QR deeplink and typed code): verify phone, then launch.

    Kept in one place so the phone-verification branch cannot drift between the two entries.
    """
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


def _normalize_code(text: str) -> str | None:
    """Fold a hand-typed code to its canonical form, or ``None`` if it is not code-shaped.

    Strips spaces/dashes, upper-cases, maps Crockford confusables, then requires exactly
    ``_CODE_LENGTH`` characters all within ``_CODE_ALPHABET``.
    """
    compact = "".join(text.split()).replace("-", "").upper().translate(_CONFUSABLES)
    if len(compact) != _CODE_LENGTH:
        return None
    if any(ch not in _CODE_ALPHABET for ch in compact):
        return None
    return compact


def is_loyalty_code(message: Message) -> dict[str, str] | bool:
    """Filter: fire only on a code-shaped text while the program is live.

    Returns ``{"loyalty_code": <normalized>}`` (injected into the handler) on a match, else
    ``False`` so the message falls through to ordinary handlers. Dark-gate lives here: when the
    program is off a typed code is treated as ordinary text.
    """
    if not settings.loyalty_enabled:
        return False
    code = _normalize_code(message.text or "")
    if code is None:
        return False
    return {"loyalty_code": code}


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

    await _start_activation(message, session, state, code)


@router.message(StateFilter(None), is_loyalty_code)
async def cmd_manual_code(
    message: Message,
    session: AsyncSession,
    state: FSMContext,
    loyalty_code: str,
) -> None:
    """Manual entry: a hand-typed box code takes the same path as a QR scan.

    ``StateFilter(None)`` keeps this off while the user is mid-dialog (order / AI chat /
    waiting_phone) — ``loyalty.router`` is registered first, so without it this would out-race
    those state handlers for a code-shaped message. Never logs the code.
    """
    await _start_activation(message, session, state, loyalty_code)


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
