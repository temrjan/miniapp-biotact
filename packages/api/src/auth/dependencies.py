"""Auth dependencies for FastAPI."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.service import get_or_create_user
from src.auth.telegram import InitDataValidationError, validate_init_data
from src.common.exceptions import ForbiddenException, UnauthorizedException
from src.config import settings
from src.database import get_db
from src.users.models import User

_security = HTTPBearer()


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    x_init_data: Annotated[str | None, Header(alias="X-Init-Data")] = None,
) -> User:
    """
    Get current authenticated user from Telegram initData.

    Validates X-Init-Data header and returns the user.
    Creates user if not exists.
    """
    if not x_init_data:
        raise UnauthorizedException("Missing X-Init-Data header")

    if not settings.telegram_bot_token:
        raise UnauthorizedException("Bot token not configured")

    try:
        init_data = validate_init_data(
            x_init_data,
            settings.telegram_bot_token,
        )
    except InitDataValidationError as e:
        raise UnauthorizedException(str(e)) from e

    if not init_data.user:
        raise UnauthorizedException("User data missing in initData")

    user = await get_or_create_user(db, init_data.user)

    if not user.is_active:
        raise ForbiddenException("User is inactive")

    return user


async def get_current_admin(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Get current admin user."""
    if not current_user.is_admin:
        raise ForbiddenException("Admin access required")
    return current_user


async def verify_admin_jwt(
    credentials: HTTPAuthorizationCredentials = Depends(_security),
) -> int:
    """Verify JWT token and return user_id. Lightweight — no DB lookup."""
    token = credentials.credentials
    for secret, allowed_type in [
        (settings.secret_key.get_secret_value(), "admin"),
        (settings.jwt_secret.get_secret_value(), "access"),
    ]:
        try:
            payload = jwt.decode(token, secret, algorithms=[settings.jwt_algorithm])
            if payload.get("type") == allowed_type:
                return int(payload.get("sub"))
        except (JWTError, ValueError, TypeError):
            continue
    raise HTTPException(status_code=401, detail="Invalid token")


# Type aliases for cleaner code
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(get_current_admin)]
