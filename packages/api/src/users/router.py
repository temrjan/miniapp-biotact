"""Users router."""

import hmac as _hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import CurrentUser
from src.config import settings
from src.database import get_db
from src.users import service
from src.users.models import User
from src.users.schemas import (
    AddressUpdate,
    LanguageUpdate,
    PhoneUpdate,
    TelegramUserCreate,
    UserProfileResponse,
    UserResponse,
)

router = APIRouter()

DbSession = Annotated[AsyncSession, Depends(get_db)]


# =============================================================================
# Bot token verification
# =============================================================================


async def verify_bot_token(x_bot_token: str = Header(...)) -> None:
    """Verify bot token from header; an unconfigured token or an empty header is refused.

    Bytes, not str: ``compare_digest`` raises on a non-ASCII str, which would be a 500.
    """
    expected = settings.telegram_bot_token
    if (
        not expected
        or not x_bot_token
        or not _hmac.compare_digest(x_bot_token.encode("utf-8"), expected.encode("utf-8"))
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid bot token",
        )


BotAuth = Depends(verify_bot_token)


# =============================================================================
# User endpoints (Mini App)
# =============================================================================


@router.get("/me", response_model=UserProfileResponse)
async def get_me(current_user: CurrentUser) -> UserProfileResponse:
    """Get current user profile."""
    return UserProfileResponse.from_user(current_user)


@router.patch("/me/phone", response_model=UserProfileResponse)
async def update_phone(
    data: PhoneUpdate,
    current_user: CurrentUser,
    db: DbSession,
) -> UserProfileResponse:
    """Update phone number."""
    user = await service.update_phone(db, current_user, data)
    return UserProfileResponse.from_user(user)


@router.patch("/me/address", response_model=UserProfileResponse)
async def update_address(
    data: AddressUpdate,
    current_user: CurrentUser,
    db: DbSession,
) -> UserProfileResponse:
    """Update delivery address."""
    user = await service.update_address(db, current_user, data)
    return UserProfileResponse.from_user(user)


@router.patch("/me/language", response_model=UserProfileResponse)
async def update_my_language(
    data: LanguageUpdate,
    current_user: CurrentUser,
    db: DbSession,
) -> UserProfileResponse:
    """Update current user language."""
    user = await service.update_language(db, current_user, data.language_code)
    return UserProfileResponse.from_user(user)


# =============================================================================
# Telegram Bot endpoints
# =============================================================================


@router.get(
    "/telegram/{telegram_id}",
    response_model=UserResponse,
    dependencies=[BotAuth],
)
async def get_user_by_telegram_id(
    telegram_id: int,
    db: DbSession,
) -> User:
    """Get user by Telegram ID (for bot)."""
    user = await service.get_by_telegram_id(db, telegram_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    return user


@router.post(
    "/telegram",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[BotAuth],
)
async def create_telegram_user(
    data: TelegramUserCreate,
    db: DbSession,
) -> User:
    """Create user from Telegram (for bot)."""
    existing = await service.get_by_telegram_id(db, data.telegram_id)
    if existing:
        return existing

    return await service.create_from_telegram(db, data)


@router.patch(
    "/telegram/{telegram_id}/language",
    response_model=UserResponse,
    dependencies=[BotAuth],
)
async def update_telegram_user_language(
    telegram_id: int,
    data: LanguageUpdate,
    db: DbSession,
) -> User:
    """Update user language (for bot)."""
    user = await service.get_by_telegram_id(db, telegram_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user = await service.update_language(db, user, data.language_code)
    return user
