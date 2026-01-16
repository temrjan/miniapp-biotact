"""Tests for keyboards."""

import pytest
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from bot.keyboards.inline import (
    get_webapp_keyboard,
    get_main_menu_keyboard,
)


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
        
        # Find WebApp button
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
        
        pytest.fail("WebApp button not found")


class TestMainMenuKeyboard:
    """Tests for main menu keyboard."""

    def test_main_menu_returns_markup(self):
        """Test that main menu keyboard returns InlineKeyboardMarkup."""
        keyboard = get_main_menu_keyboard()
        assert isinstance(keyboard, InlineKeyboardMarkup)

    def test_main_menu_has_multiple_buttons(self):
        """Test that main menu has multiple buttons."""
        keyboard = get_main_menu_keyboard()
        
        total_buttons = sum(len(row) for row in keyboard.inline_keyboard)
        assert total_buttons >= 3

    def test_main_menu_has_my_orders(self):
        """Test that main menu has my_orders callback."""
        keyboard = get_main_menu_keyboard()
        
        for row in keyboard.inline_keyboard:
            for btn in row:
                if btn.callback_data == "my_orders":
                    return
        
        pytest.fail("my_orders button not found")

    def test_main_menu_has_help(self):
        """Test that main menu has help callback."""
        keyboard = get_main_menu_keyboard()
        
        for row in keyboard.inline_keyboard:
            for btn in row:
                if btn.callback_data == "help":
                    return
        
        pytest.fail("help button not found")
