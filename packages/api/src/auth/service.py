"""Auth service layer."""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.users.models import User
from src.auth.schemas import TelegramUser


async def get_user_by_telegram_id(
    db: AsyncSession,
    telegram_id: int,
) -> Optional[User]:
    """Get user by Telegram ID."""
    result = await db.execute(
        select(User).where(User.telegram_id == telegram_id)
    )
    return result.scalar_one_or_none()


async def create_user_from_telegram(
    db: AsyncSession,
    telegram_user: TelegramUser,
) -> User:
    """Create new user from Telegram data."""
    user = User(
        telegram_id=telegram_user.id,
        username=telegram_user.username,
        first_name=telegram_user.first_name,
        last_name=telegram_user.last_name,
        language_code=telegram_user.language_code or "ru",
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def get_or_create_user(
    db: AsyncSession,
    telegram_user: TelegramUser,
) -> User:
    """Get existing user or create new one from Telegram data."""
    user = await get_user_by_telegram_id(db, telegram_user.id)
    
    if user:
        # Update user data from Telegram (name might change)
        user.username = telegram_user.username
        user.first_name = telegram_user.first_name
        user.last_name = telegram_user.last_name
        if telegram_user.language_code:
            user.language_code = telegram_user.language_code
        await db.flush()
        return user
    
    return await create_user_from_telegram(db, telegram_user)
