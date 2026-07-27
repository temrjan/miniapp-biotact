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
    """Verify an ADMIN JWT and return user_id. Lightweight — no DB lookup.

    Only the admin token opens the admin surface: ``type="admin"``, signed with ``secret_key``,
    issued by ``/admin/auth/login`` (:func:`src.auth.admin_router.create_admin_token`).

    PR-S removed a second accepted token (``type="access"`` signed with ``jwt_secret``). It carried
    no role at all, so anyone able to obtain one from the self-registration circuit reached every
    endpoint guarded by this dependency; that circuit is gone with it.

    Decoded with ``settings.algorithm`` — the very setting the issuer signs with. The previous code
    decoded with ``settings.jwt_algorithm``, a separate field that merely happened to hold the same
    ``"HS256"``: it worked by coincidence, not by contract.
    """
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.secret_key.get_secret_value(),
            algorithms=[settings.algorithm],
        )
        sub = payload.get("sub")
        if payload.get("type") == "admin" and sub is not None:
            return int(sub)
    except (JWTError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc
    raise HTTPException(status_code=401, detail="Invalid token")


# Type aliases for cleaner code
CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(get_current_admin)]


async def require_admin(
    db: Annotated[AsyncSession, Depends(get_db)],
    credentials: HTTPAuthorizationCredentials = Depends(_security),
) -> User:
    """Strong admin gate: an admin-typed token AND ``is_admin`` confirmed in the database.

    Use this — not :func:`verify_admin_jwt` — for operations that change state irreversibly.
    ``verify_admin_jwt`` is deliberately lightweight (no DB round-trip), so it cannot notice a
    revoked account or a token whose subject no longer exists; freezing a lottery draw must not
    hang on a check that weak.

    This is the CANONICAL copy. Five functionally identical ones live in ``analytics/router.py``,
    ``crm/router.py``, ``crm/admin_router.py``, ``broadcasts/admin_router.py`` and
    ``users/admin_router.py``; they already differ cosmetically, which is how drift starts.
    Collapsing them onto this one is a backlog item — deliberately NOT done here, so that PR-5.3
    stays additive and no working router changes.
    """
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.secret_key.get_secret_value(),
            algorithms=[settings.algorithm],
        )
    except JWTError as exc:
        raise UnauthorizedException("Invalid or expired token") from exc

    if payload.get("type") != "admin":
        raise ForbiddenException("Not an admin token")

    sub = payload.get("sub")
    if sub is None:
        raise ForbiddenException("Admin access required")
    user = await db.get(User, int(sub))
    if user is None or not user.is_admin:
        raise ForbiddenException("Admin access required")
    return user
