"""Tests for bot handlers."""

import pytest
from unittest.mock import AsyncMock, patch

from bot.handlers.common import cmd_start, cmd_help, callback_my_orders, callback_help


class TestStartCommand:
    """Tests for /start command."""

    @pytest.mark.asyncio
    async def test_start_sends_welcome(self, message):
        """Test that /start sends welcome message."""
        await cmd_start(message)
        
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        
        # Check that welcome text contains user name
        assert "Test" in call_args[0][0]
        assert "Biotact" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_start_has_keyboard(self, message):
        """Test that /start message has inline keyboard."""
        await cmd_start(message)
        
        call_kwargs = message.answer.call_args[1]
        assert "reply_markup" in call_kwargs
        assert call_kwargs["reply_markup"] is not None


class TestHelpCommand:
    """Tests for /help command."""

    @pytest.mark.asyncio
    async def test_help_sends_info(self, message):
        """Test that /help sends help info."""
        await cmd_help(message)
        
        message.answer.assert_called_once()
        call_args = message.answer.call_args
        
        # Check that help text contains key info
        assert "Справка" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_help_has_keyboard(self, message):
        """Test that /help message has keyboard."""
        await cmd_help(message)
        
        call_kwargs = message.answer.call_args[1]
        assert "reply_markup" in call_kwargs


class TestCallbacks:
    """Tests for callback handlers."""

    @pytest.mark.asyncio
    async def test_my_orders_callback(self, callback_query):
        """Test my_orders callback."""
        callback_query.data = "my_orders"
        
        await callback_my_orders(callback_query)
        
        callback_query.message.edit_text.assert_called_once()
        callback_query.answer.assert_called_once()
        
        # Check text contains orders info
        call_args = callback_query.message.edit_text.call_args
        assert "заказ" in call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_help_callback(self, callback_query):
        """Test help callback."""
        callback_query.data = "help"
        
        await callback_help(callback_query)
        
        callback_query.message.edit_text.assert_called_once()
        callback_query.answer.assert_called_once()
