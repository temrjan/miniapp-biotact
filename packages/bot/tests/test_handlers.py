"""Tests for bot handlers."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bot.handlers.common import cmd_start, cmd_help


@pytest.fixture
def mock_session():
    """Create mock async database session."""
    return AsyncMock()


@pytest.fixture
def mock_state():
    """Create mock FSM state context."""
    return AsyncMock()


@pytest.fixture
def mock_user_service():
    """Create mock UserService that returns a test user."""
    with patch("bot.handlers.common.UserService") as MockUserService:
        service = AsyncMock()
        mock_user = MagicMock()
        mock_user.language = "ru"
        mock_user.language_code = "ru"
        mock_user.first_name = "Test"
        service.get_or_create.return_value = (mock_user, False)
        MockUserService.return_value = service
        yield service


class TestStartCommand:
    """Tests for /start command."""

    @pytest.mark.asyncio
    async def test_start_clears_state(self, message, mock_session, mock_state, mock_user_service):
        """Test that /start clears FSM state."""
        await cmd_start(message, mock_session, mock_state)
        mock_state.clear.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_sends_message(self, message, mock_session, mock_state, mock_user_service):
        """Test that /start sends a message."""
        await cmd_start(message, mock_session, mock_state)
        message.answer.assert_called()


class TestHelpCommand:
    """Tests for /help command."""

    @pytest.mark.asyncio
    async def test_help_sends_message(self, message, mock_session, mock_user_service):
        """Test that /help sends help info."""
        await cmd_help(message, mock_session)
        message.answer.assert_called_once()
