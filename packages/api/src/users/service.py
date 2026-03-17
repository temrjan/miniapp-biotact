"""Users service layer."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.users.models import User
from src.users.schemas import AddressUpdate, PhoneUpdate, TelegramUserCreate


async def get_by_telegram_id(db: AsyncSession, telegram_id: int) -> User | None:
    """Get user by Telegram ID."""
    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    return result.scalar_one_or_none()


async def create_from_telegram(db: AsyncSession, data: TelegramUserCreate) -> User:
    """Create user from Telegram data."""
    user = User(
        telegram_id=data.telegram_id,
        username=data.username,
        first_name=data.first_name,
        last_name=data.last_name,
        language_code=data.language_code,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


async def update_language(db: AsyncSession, user: User, language_code: str) -> User:
    """Update user language."""
    user.language_code = language_code
    await db.flush()
    await db.refresh(user)
    return user


async def update_phone(db: AsyncSession, user: User, data: PhoneUpdate) -> User:
    """Update user phone."""
    user.phone = data.phone
    await db.flush()
    await db.refresh(user)
    return user


async def update_address(db: AsyncSession, user: User, data: AddressUpdate) -> User:
    """Update user address."""
    user.address_street = data.street
    user.address_house = data.house
    user.address_apartment = data.apartment
    user.address_entrance = data.entrance
    user.address_floor = data.floor
    user.address_comment = data.comment
    user.address_lat = data.lat
    user.address_lng = data.lng
    await db.flush()
    await db.refresh(user)
    return user
