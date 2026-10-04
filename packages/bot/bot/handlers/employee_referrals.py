"""Prefix-specific launcher runs before the generic auth deep-link handler."""

from aiogram import F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.core.config import settings
from bot.handlers.common import cmd_start
from bot.referral_entry import parse_intent

router = Router(name="employee_referrals")


@router.message(CommandStart(deep_link=True, magic=F.args.startswith("ref_")))
async def referral_start(
    message: Message, command: CommandObject, session: AsyncSession, state: FSMContext
) -> None:
    if message.chat.type != "private":
        return
    if not settings.employee_referral_entry_enabled:
        await cmd_start(message, session, state)
        return
    await cmd_start(message, session, state, referral_intent=parse_intent(command.args or ""))
