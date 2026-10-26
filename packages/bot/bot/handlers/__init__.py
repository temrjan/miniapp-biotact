"""Handlers module."""

from aiogram import Router

from bot.handlers import auth, common, employee_referrals, loyalty, web_login

# Main router
router = Router(name="main")

# Include all handlers. loyalty first: its qr_ deeplink filter is specific, so it must run
# before auth's generic deep_link handler (which would otherwise consume qr_ deeplinks).
router.include_routers(
    web_login.router,
    employee_referrals.router,
    loyalty.router,
    auth.router,
    common.router,
)

__all__ = ["router"]
