"""Bot core modules."""

from bot.core.config import settings
from bot.core.loader import bot, dp

__all__ = ["bot", "dp", "settings"]
