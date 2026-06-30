"""User service for direct database operations."""

import logging
from datetime import UTC, datetime
from typing import Literal

from aiogram.types import User as TelegramUser
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.models.user import User

logger = logging.getLogger(__name__)


class UserService:
    """Service for user-related database operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        """Get user by telegram_id."""
        result = await self.session.execute(select(User).where(User.telegram_id == telegram_id))
        return result.scalar_one_or_none()

    async def create(self, telegram_user: TelegramUser) -> User:
        """Create new user from Telegram user data."""
        user = User(
            telegram_id=telegram_user.id,
            username=telegram_user.username,
            first_name=telegram_user.first_name,
            last_name=telegram_user.last_name,
            language_code=telegram_user.language_code or "ru",
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def get_or_create(self, telegram_user: TelegramUser) -> tuple[User, bool]:
        """Get existing user or create new one.

        Returns:
            Tuple of (user, is_new) where is_new is True if user was created.
        """
        user = await self.get_by_telegram_id(telegram_user.id)
        if user:
            return user, False

        try:
            user = await self.create(telegram_user)
            return user, True
        except IntegrityError:
            logger.warning(
                "Race condition in get_or_create for telegram_id=%s, fetching existing user",
                telegram_user.id,
            )
            await self.session.rollback()
            user = await self.get_by_telegram_id(telegram_user.id)
            return user, False

    async def update_language(
        self,
        telegram_id: int,
        language_code: Literal["ru", "uz"],
    ) -> User | None:
        """Update user's language preference."""
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            user.language_code = language_code
            await self.session.flush()
        return user

    async def update_phone(self, telegram_id: int, phone: str) -> User | None:
        """Update user's phone number (unverified — e.g. typed manually)."""
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            user.phone = phone
            await self.session.flush()
        return user

    async def set_verified_phone(self, telegram_id: int, phone: str) -> User | None:
        """Store a phone shared via Telegram contact and stamp it as verified.

        Used by the Biotact Club QR-deeplink flow: a contact share is an explicit
        affirmative act, so both ``phone`` and ``phone_verified_at`` are set together.
        """
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            user.phone = phone
            user.phone_verified_at = datetime.now(UTC)
            await self.session.flush()
        return user

    async def get_phone(self, telegram_id: int) -> str | None:
        """Get user's phone number if saved."""
        user = await self.get_by_telegram_id(telegram_id)
        return user.phone if user else None
