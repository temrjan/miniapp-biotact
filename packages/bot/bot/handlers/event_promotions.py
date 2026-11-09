"""Event QR entry ``/start ev_<code>`` and the ``/qr_stats`` report (spec event-qr-discount).

Registered before ``auth.router``: its generic deep-link handler consumes every other deep link
silently (R-30).
"""

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.config import settings
from bot.handlers.common import cmd_start
from bot.services.event_promotions import EVENT_PREFIX, promotion_code, record_scan
from bot.services.event_stats import promotion_stats, render_report, split_message
from bot.services.user import UserService

logger = logging.getLogger(__name__)

router = Router(name="event_promotions")


@router.message(CommandStart(deep_link=True, magic=F.args.startswith(EVENT_PREFIX)))
async def event_start(
    message: Message, command: CommandObject, session: AsyncSession, state: FSMContext
) -> None:
    """Record the scan, grant inside the window, then run the ordinary steps (R-30…R-33).

    Every path ends in the ordinary ``/start`` flow; only an active grant adds the event intent.
    """
    if message.chat.type != "private":
        return
    code = promotion_code(command.args or "")
    if not settings.event_promotions_enabled or code is None:
        await cmd_start(message, session, state)
        return
    # The single user resolution of this update (R-32): its is_new drives the grant and the steps.
    user, is_new = await UserService(session).get_or_create(message.from_user)
    scan = None
    try:
        async with session.begin_nested():
            scan = await record_scan(session, user, is_new, code)
    except SQLAlchemyError:
        # The savepoint rolled back the scan and grant only; the user row stays (R-31).
        logger.warning("Event promotion scan failed")
    intent = None
    if scan is not None and scan[0] != "outside_window":
        intent = scan[1]
    await cmd_start(message, session, state, resolved_user=(user, is_new), promotion_intent=intent)


@router.message(Command("qr_stats"))
async def qr_stats(message: Message, session: AsyncSession) -> None:
    """Promotion effectiveness for administrators only; anyone else gets nothing (R-40, R-41)."""
    if message.chat.type != "private" or message.from_user is None:
        return
    if message.from_user.id not in settings.admin_ids:
        return
    report = render_report(await promotion_stats(session))
    for part in split_message(report):
        await message.answer(part, parse_mode=None)
