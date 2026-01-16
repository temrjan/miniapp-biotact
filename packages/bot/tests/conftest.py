"""Pytest configuration and fixtures."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from aiogram import Bot
from aiogram.types import User, Chat, Message, CallbackQuery


@pytest.fixture
def bot():
    """Create mock bot."""
    return AsyncMock(spec=Bot)


@pytest.fixture
def user():
    """Create test user."""
    return User(
        id=123456789,
        is_bot=False,
        first_name="Test",
        last_name="User",
        username="testuser",
        language_code="ru",
    )


@pytest.fixture
def chat():
    """Create test chat."""
    return Chat(
        id=123456789,
        type="private",
    )


@pytest.fixture
def message(user, chat, bot):
    """Create test message."""
    msg = MagicMock(spec=Message)
    msg.from_user = user
    msg.chat = chat
    msg.bot = bot
    msg.text = "/start"
    msg.answer = AsyncMock()
    msg.reply = AsyncMock()
    msg.edit_text = AsyncMock()  # Add edit_text async mock
    return msg


@pytest.fixture
def callback_query(user, chat, bot, message):
    """Create test callback query."""
    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = user
    callback.message = message
    callback.message.edit_text = AsyncMock()  # Make edit_text async
    callback.data = "test_callback"
    callback.answer = AsyncMock()
    return callback
