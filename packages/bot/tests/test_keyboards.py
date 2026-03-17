"""Tests for keyboards."""

from aiogram.types import InlineKeyboardMarkup, WebAppInfo

from bot.keyboards.inline import get_webapp_keyboard


class TestWebAppKeyboard:
    """Tests for WebApp keyboard."""

    def test_webapp_keyboard_returns_markup(self):
        """Test that webapp keyboard returns InlineKeyboardMarkup."""
        keyboard = get_webapp_keyboard()
        assert isinstance(keyboard, InlineKeyboardMarkup)

    def test_webapp_keyboard_has_webapp_button(self):
        """Test that keyboard has WebApp button."""
        keyboard = get_webapp_keyboard()
        buttons = keyboard.inline_keyboard

        assert len(buttons) > 0

        webapp_button = None
        for row in buttons:
            for btn in row:
                if btn.web_app is not None:
                    webapp_button = btn
                    break

        assert webapp_button is not None
        assert isinstance(webapp_button.web_app, WebAppInfo)

    def test_webapp_keyboard_has_correct_url(self):
        """Test that WebApp button has correct URL."""
        keyboard = get_webapp_keyboard()

        for row in keyboard.inline_keyboard:
            for btn in row:
                if btn.web_app is not None:
                    assert "biotact.uz" in btn.web_app.url
                    return

        raise AssertionError("WebApp button not found")

    def test_webapp_keyboard_accepts_language(self):
        """Test that keyboard accepts language parameter."""
        keyboard_ru = get_webapp_keyboard("ru")
        keyboard_uz = get_webapp_keyboard("uz")
        assert isinstance(keyboard_ru, InlineKeyboardMarkup)
        assert isinstance(keyboard_uz, InlineKeyboardMarkup)
