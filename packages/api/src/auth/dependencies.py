"""Auth dependencies for FastAPI."""

import re
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
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


def _admin_subject(credentials: HTTPAuthorizationCredentials, *, lightweight: bool = False) -> int:
    """Validate claims before touching SQL, preserving both existing refusal conventions."""
    try:
        if credentials.scheme.lower() != "bearer":
            raise ValueError("Invalid scheme")
        payload: dict[str, object] = jwt.decode(
            credentials.credentials,
            settings.secret_key.get_secret_value(),
            algorithms=[settings.algorithm],
            options={"require_exp": True, "verify_sub": False},
        )
        if type(payload.get("exp")) is not int:
            raise ValueError("Invalid expiration")
    except (JWTError, ValueError, TypeError, OverflowError):
        if lightweight:
            raise HTTPException(status_code=401, detail="Invalid token") from None
        raise UnauthorizedException("Invalid or expired token") from None

    if payload.get("type") != "admin" or "sub" not in payload:
        if lightweight:
            raise HTTPException(status_code=401, detail="Invalid token")
        raise ForbiddenException(
            "Not an admin token" if payload.get("type") != "admin" else "Admin access required"
        )
    sub = payload["sub"]
    if not isinstance(sub, str) or not re.fullmatch(r"[1-9][0-9]{0,9}", sub):
        raise UnauthorizedException("Invalid token")
    user_id = int(sub)
    if user_id > 2147483647:
        raise UnauthorizedException("Invalid token")
    return user_id


async def _current_admin(
    db: AsyncSession, credentials: HTTPAuthorizationCredentials, *, lightweight: bool = False
) -> User:
    user_id = _admin_subject(credentials, lightweight=lightweight)
    user = await db.scalar(
        select(User).where(User.id == user_id).execution_options(populate_existing=True)
    )
    if user is None or not user.is_admin or not user.is_active:
        raise ForbiddenException("Admin access required")
    return user


async def verify_admin_jwt(
    db: Annotated[AsyncSession, Depends(get_db)],
    credentials: HTTPAuthorizationCredentials = Depends(_security),
) -> int:
    """Legacy integer-returning adapter; current rights are checked on every request."""
    return (await _current_admin(db, credentials, lightweight=True)).id


# Customer initData aliases stay unchanged.
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(get_current_admin)]


async def require_admin(
    db: Annotated[AsyncSession, Depends(get_db)],
    credentials: HTTPAuthorizationCredentials = Depends(_security),
) -> User:
    """Canonical admin JWT AND current active administrator rule."""
    return await _current_admin(db, credentials)


async def require_admin_header(db: AsyncSession, authorization: str | None) -> User:
    """Adapter for the existing Header-based router dependencies and /auth/me."""
    if not authorization or not authorization.startswith("Bearer "):
        raise UnauthorizedException("Missing authorization header")
    return await require_admin(
        db=db,
        credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=authorization[7:]),
    )
