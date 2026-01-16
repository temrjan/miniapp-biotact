"""Handlers module."""
from aiogram import Router

from bot.handlers import common, auth

# Main router
router = Router(name="main")

# Include all handlers (auth first to catch deep links)
router.include_routers(
    auth.router,
    common.router,
)

__all__ = ["router"]
