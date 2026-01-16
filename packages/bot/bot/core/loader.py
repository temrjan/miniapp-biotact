"""Bot and Dispatcher instances."""

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.core.config import settings


def get_storage():
    """Get FSM storage."""
    return MemoryStorage()


# Bot instance
bot = Bot(
    token=settings.bot_token.get_secret_value(),
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML,
        link_preview_is_disabled=True,
    ),
)

# Dispatcher instance
dp = Dispatcher(
    storage=get_storage(),
    settings=settings,
)
