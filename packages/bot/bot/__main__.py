"""Bot entry point."""

import asyncio
import logging
import sys

from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    ErrorEvent,
    MenuButtonDefault,
)

from bot.core.config import settings
from bot.core.loader import bot, dp
from bot.handlers import router
from bot.middlewares import DatabaseMiddleware, LoggingMiddleware
from bot.middlewares.referral_entry import ReferralEntryMiddleware, is_ref_update

logger = logging.getLogger(__name__)


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
    dp.update.outer_middleware(ReferralEntryMiddleware())
    dp.update.outer_middleware(LoggingMiddleware())
    dp.update.outer_middleware(DatabaseMiddleware())

    # Global error handler
    @dp.errors()
    async def error_handler(event: ErrorEvent) -> bool:
        if is_ref_update(event.update):
            logger.error("Referral entry update failed")
        else:
            logger.exception(
                "Unhandled error in update %s: %s",
                event.update.update_id,
                event.exception,
            )
        # Try to notify the user
        try:
            update = event.update
            chat_id = None
            if update.message:
                chat_id = update.message.chat.id
            elif update.callback_query:
                chat_id = update.callback_query.message.chat.id

            if chat_id:
                await bot.send_message(
                    chat_id,
                    "Произошла ошибка. Попробуйте позже или нажмите /start",
                )
        except Exception:
            pass
        return True

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
