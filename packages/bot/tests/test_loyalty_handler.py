"""Tests for Biotact Club QR-deeplink loyalty handlers (Stage 4, PR-4.1).

The bot CI runs both ``ruff check bot/`` and ``pytest tests/`` (ci.yml Bot job), so these run in
CI: deeplink dispatch (qr_ vs auth_), contact -> verified phone, the dark gate, and the
code-carrying mini-app launcher.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.filters import CommandObject, StateFilter
from magic_filter import F

from bot.core.config import settings
from bot.handlers.loyalty import (
    QR_PREFIX,
    LoyaltyActivationState,
    _normalize_code,
    cmd_manual_code,
    cmd_start_qr,
    is_loyalty_code,
    process_loyalty_contact,
    router,
)
from bot.keyboards.inline import get_loyalty_webapp_keyboard
from bot.models.user import User
from bot.services.user import UserService


def _webapp_url(markup) -> str:
    """Pull the web_app URL out of a single-button inline keyboard."""
    return markup.inline_keyboard[0][0].web_app.url


def _command(args: str | None) -> CommandObject:
    return CommandObject(command="start", args=args)


# --------------------------------------------------------------------------- #
# Deeplink dispatch — qr_ routes here, auth_ does not (magic prefix filter)
# --------------------------------------------------------------------------- #


def test_qr_magic_filter_matches_qr_not_auth() -> None:
    """The qr_ prefix filter accepts qr_ deeplinks and rejects auth_/other ones."""
    magic = F.args.startswith(QR_PREFIX)
    assert magic.resolve(_command("qr_ABC123"))
    assert not magic.resolve(_command("auth_ABC123"))
    assert not magic.resolve(_command("random"))


# --------------------------------------------------------------------------- #
# Keyboard — the launcher carries the code in the URL query
# --------------------------------------------------------------------------- #


def test_loyalty_webapp_keyboard_carries_code() -> None:
    """Both locales build a web_app button whose URL carries ?code=."""
    assert "code=ABC123" in _webapp_url(get_loyalty_webapp_keyboard("ABC123", "ru"))
    assert "code=ABC123" in _webapp_url(get_loyalty_webapp_keyboard("ABC123", "uz"))


def test_loyalty_webapp_keyboard_url_encodes_code() -> None:
    """A code with URL-special chars is percent-encoded, keeping the URL valid."""
    url = _webapp_url(get_loyalty_webapp_keyboard("a b&c", "ru"))
    assert "a b&c" not in url
    assert "code=a%20b%26c" in url


# --------------------------------------------------------------------------- #
# Service — set_verified_phone stamps both phone and phone_verified_at
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_set_verified_phone_sets_phone_and_timestamp() -> None:
    """A shared contact sets the phone AND marks it verified."""
    user = User(telegram_id=123456789, first_name="Test")
    service = UserService(AsyncMock())
    service.get_by_telegram_id = AsyncMock(return_value=user)

    result = await service.set_verified_phone(123456789, "+998901234567")

    assert result is user
    assert user.phone == "+998901234567"
    assert user.phone_verified_at is not None


@pytest.mark.asyncio
async def test_set_verified_phone_unknown_user_returns_none() -> None:
    """No user row → no write, returns None."""
    service = UserService(AsyncMock())
    service.get_by_telegram_id = AsyncMock(return_value=None)
    assert await service.set_verified_phone(999, "+998901234567") is None


# --------------------------------------------------------------------------- #
# cmd_start_qr — dark gate, contact request, direct launch
# --------------------------------------------------------------------------- #


@pytest.fixture
def mock_session() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_state() -> AsyncMock:
    return AsyncMock()


@pytest.mark.asyncio
async def test_qr_dark_gate_delegates_to_normal_start(
    message, mock_session, mock_state, monkeypatch
) -> None:
    """While loyalty_enabled is off, qr_ behaves like a normal /start (code ignored)."""
    monkeypatch.setattr(settings, "loyalty_enabled", False)
    with patch("bot.handlers.loyalty.cmd_start", new=AsyncMock()) as mock_cmd_start:
        await cmd_start_qr(message, _command("qr_ABC123"), mock_session, mock_state)
    mock_cmd_start.assert_awaited_once_with(message, mock_session, mock_state)
    message.answer.assert_not_called()


@pytest.mark.asyncio
async def test_qr_on_without_verified_phone_asks_contact_and_stores_code(
    message, mock_session, mock_state, monkeypatch
) -> None:
    """Flag on, no verified phone → stash code in FSM and request a contact."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    with patch("bot.handlers.loyalty.UserService") as mock_us:
        service = AsyncMock()
        service.get_or_create.return_value = (
            MagicMock(phone_verified_at=None, language_code="ru"),
            False,
        )
        mock_us.return_value = service
        await cmd_start_qr(message, _command("qr_ABC123"), mock_session, mock_state)

    mock_state.set_state.assert_awaited_once_with(LoyaltyActivationState.waiting_phone)
    mock_state.update_data.assert_awaited_once_with(loyalty_code="ABC123", lang="ru")
    message.answer.assert_called_once()


@pytest.mark.asyncio
async def test_qr_on_with_verified_phone_launches_with_code(
    message, mock_session, mock_state, monkeypatch
) -> None:
    """Flag on, already-verified phone → launch the mini app straight away with the code."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    with patch("bot.handlers.loyalty.UserService") as mock_us:
        service = AsyncMock()
        service.get_or_create.return_value = (
            MagicMock(phone_verified_at=object(), language_code="ru"),
            False,
        )
        mock_us.return_value = service
        await cmd_start_qr(message, _command("qr_ABC123"), mock_session, mock_state)

    mock_state.set_state.assert_not_called()
    markup = message.answer.call_args.kwargs["reply_markup"]
    assert "code=ABC123" in _webapp_url(markup)


# --------------------------------------------------------------------------- #
# process_loyalty_contact — verified phone + code-carrying launch
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_loyalty_contact_sets_verified_phone_and_launches(message, mock_session) -> None:
    """The sender's own contact is normalized, stored verified, then the mini app launches."""
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={"loyalty_code": "ABC123", "lang": "ru"})
    # Own contact: user_id matches the sender (what the request_contact button returns).
    message.contact = MagicMock(phone_number="998901234567", user_id=123456789)

    with patch("bot.handlers.loyalty.UserService") as mock_us:
        service = AsyncMock()
        mock_us.return_value = service
        await process_loyalty_contact(message, mock_session, state)

    service.set_verified_phone.assert_awaited_once_with(123456789, "+998901234567")
    state.clear.assert_awaited_once()
    markup = message.answer.call_args.kwargs["reply_markup"]
    assert "code=ABC123" in _webapp_url(markup)


@pytest.mark.asyncio
async def test_loyalty_contact_rejects_foreign_contact(message, mock_session) -> None:
    """An attached/foreign contact (user_id != sender) is NOT stamped verified — re-ask instead."""
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={"loyalty_code": "ABC123", "lang": "ru"})
    # Spoofed: an arbitrary contact card carrying someone else's number, different user_id.
    message.contact = MagicMock(phone_number="998900000000", user_id=999)

    with patch("bot.handlers.loyalty.UserService") as mock_us:
        service = AsyncMock()
        mock_us.return_value = service
        await process_loyalty_contact(message, mock_session, state)

    service.set_verified_phone.assert_not_called()
    state.clear.assert_not_called()
    message.answer.assert_called_once()  # re-prompt for the user's own contact


# --------------------------------------------------------------------------- #
# Manual code entry — normalization, code-shaped filter, handler, state gating
# --------------------------------------------------------------------------- #

# A real generated code (Crockford base32, 12 chars) used across manual-entry tests.
_VALID_CODE = "PNS6DRSDMQD1"


def test_normalize_code_accepts_canonical() -> None:
    """A clean 12-char Crockford code passes through unchanged."""
    assert _normalize_code(_VALID_CODE) == _VALID_CODE


def test_normalize_code_folds_case_and_separators() -> None:
    """Lower-case, spaces and dashes are folded to the canonical form (human-typed tolerance)."""
    assert _normalize_code("pns6drsdmqd1") == _VALID_CODE
    assert _normalize_code("PNS6-DRSD-MQD1") == _VALID_CODE
    assert _normalize_code("PNS6 DRSD MQD1") == _VALID_CODE


def test_normalize_code_maps_crockford_confusables() -> None:
    """O->0, I->1, L->1 (the chars the alphabet omits) are remapped, keeping the code valid."""
    # 'ONS6DRSDMQDL' -> O->0, L->1 -> '0NS6DRSDMQD1'
    assert _normalize_code("ONS6DRSDMQDL") == "0NS6DRSDMQD1"


def test_normalize_code_rejects_wrong_length() -> None:
    """Not exactly 12 chars → not code-shaped."""
    assert _normalize_code("ABC") is None
    assert _normalize_code(_VALID_CODE + "2") is None


def test_normalize_code_rejects_u_and_junk() -> None:
    """U is not in the alphabet and is not remapped; other stray chars also reject."""
    assert _normalize_code("UNS6DRSDMQD1") is None  # U -> invalid
    assert _normalize_code("PNS6DRSDMQD@") is None  # symbol -> invalid


def test_is_loyalty_code_dark_gate_returns_false(message, monkeypatch) -> None:
    """While the program is off, even a valid code falls through (filter returns False)."""
    monkeypatch.setattr(settings, "loyalty_enabled", False)
    message.text = _VALID_CODE
    assert is_loyalty_code(message) is False


def test_is_loyalty_code_live_returns_normalized(message, monkeypatch) -> None:
    """Live + code-shaped text → inject the normalized code for the handler."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    message.text = "pns6-drsd-mqd1"
    assert is_loyalty_code(message) == {"loyalty_code": _VALID_CODE}


def test_is_loyalty_code_rejects_non_codes(message, monkeypatch) -> None:
    """Live but not code-shaped (word / admin 6-hex / None) → False, message falls through."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    for text in ["привет", "A1B2C3", "hello there", ""]:
        message.text = text
        assert is_loyalty_code(message) is False
    message.text = None
    assert is_loyalty_code(message) is False


@pytest.mark.asyncio
async def test_manual_code_with_verified_phone_launches(
    message, mock_session, mock_state, monkeypatch
) -> None:
    """A typed code from a verified user launches the mini app straight away (same as a scan)."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    with patch("bot.handlers.loyalty.UserService") as mock_us:
        service = AsyncMock()
        service.get_or_create.return_value = (
            MagicMock(phone_verified_at=object(), language_code="ru"),
            False,
        )
        mock_us.return_value = service
        await cmd_manual_code(message, mock_session, mock_state, loyalty_code=_VALID_CODE)

    mock_state.set_state.assert_not_called()
    markup = message.answer.call_args.kwargs["reply_markup"]
    assert f"code={_VALID_CODE}" in _webapp_url(markup)


@pytest.mark.asyncio
async def test_manual_code_without_verified_phone_asks_contact(
    message, mock_session, mock_state, monkeypatch
) -> None:
    """A typed code from an unverified user stashes the code and requests a contact (same as scan)."""
    monkeypatch.setattr(settings, "loyalty_enabled", True)
    with patch("bot.handlers.loyalty.UserService") as mock_us:
        service = AsyncMock()
        service.get_or_create.return_value = (
            MagicMock(phone_verified_at=None, language_code="ru"),
            False,
        )
        mock_us.return_value = service
        await cmd_manual_code(message, mock_session, mock_state, loyalty_code=_VALID_CODE)

    mock_state.set_state.assert_awaited_once_with(LoyaltyActivationState.waiting_phone)
    mock_state.update_data.assert_awaited_once_with(loyalty_code=_VALID_CODE, lang="ru")
    message.answer.assert_called_once()


def test_manual_code_handler_is_state_gated_to_none() -> None:
    """cmd_manual_code is registered with StateFilter(None) so it stays off mid-dialog.

    Without this, loyalty.router (included first) would out-race the OrderState/AIAssistantState
    F.text handlers for a code-shaped message. Removing StateFilter(None) turns this red.
    """
    handler = next(h for h in router.message.handlers if h.callback is cmd_manual_code)
    state_filters = [
        getattr(f, "callback", f)
        for f in (handler.filters or [])
        if isinstance(getattr(f, "callback", f), StateFilter)
    ]
    assert state_filters, "cmd_manual_code must carry a StateFilter"
    assert any(None in sf.states for sf in state_filters), "StateFilter(None) required"
