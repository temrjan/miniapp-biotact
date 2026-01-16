"""Bot entry point."""

import asyncio
import logging
import sys

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    MenuButtonDefault,
)

from bot.core.config import settings
from bot.core.loader import bot, dp
from bot.handlers import router
from bot.middlewares import DatabaseMiddleware, LoggingMiddleware


async def set_commands(bot: Bot) -> None:
    """Set bot commands in menu."""
    commands = [
        BotCommand(command="start", description="🚀 Главное меню"),
        BotCommand(command="help", description="❓ Помощь"),
        BotCommand(command="language", description="🌐 Сменить язык"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())


async def set_menu_button(bot: Bot) -> None:
    """Set default menu button (commands list)."""
    # Menu button — стандартное меню команд
    # В будущем здесь будет AskBiotact AI чат-бот
    await bot.set_chat_menu_button(menu_button=MenuButtonDefault())


async def on_startup(bot: Bot) -> None:
    """Actions on bot startup."""
    await set_commands(bot)
    await set_menu_button(bot)

    # Notify admins about startup
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, "🟢 Biotact Bot запущен")
        except Exception:
            pass

    logging.info("Bot started successfully")


async def on_shutdown(bot: Bot) -> None:
    """Actions on bot shutdown."""
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, "🔴 Biotact Bot остановлен")
        except Exception:
            pass

    logging.info("Bot stopped")


async def main() -> None:
    """Main function."""
    # Setup logging
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stdout,
    )

    # Register middlewares (order matters: logging first, then database)
    dp.update.outer_middleware(LoggingMiddleware())
    dp.update.outer_middleware(DatabaseMiddleware())

    # Register startup/shutdown handlers
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Include routers
    dp.include_router(router)

    # Start polling
    logging.info("Starting bot polling...")
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
