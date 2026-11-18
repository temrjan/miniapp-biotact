"""Admin auth via code - alternative to Telegram Login Widget."""

import hmac as _hmac
import secrets
import time
from typing import Annotated, Any

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import require_admin_header
from src.auth.models import AdminPasswordAccount
from src.auth.passwords import (
    COMPAT_LOGIN,
    DUMMY_HASH,
    PasswordServiceUnavailable,
    VerificationCapacity,
    check_hash,
    normalize_login,
    password_bytes,
)
from src.buyer_auth.router import peer
from src.config import settings
from src.database import get_db
from src.users.models import User

router = APIRouter(prefix="/admin/auth", tags=["admin-auth"])

# In-memory store for auth codes (in production use Redis)
# Format: {code: {"created_at": timestamp, "telegram_id": None or int, "user_data": None or dict}}
AUTH_CODES: dict[str, dict[str, Any]] = {}

# Code expiration time (5 minutes)
CODE_EXPIRY_SECONDS = 300

# Whitelist of admin Telegram IDs
ADMIN_TELEGRAM_IDS: set[int] = {
    8503214095,  # Main admin
}

# =============================================================================
# Rate Limiting for Login (Brute Force Protection)
# =============================================================================

# Rate limit settings
MAX_LOGIN_ATTEMPTS = 5  # Max attempts before lockout
LOCKOUT_SECONDS = 900  # 15 minutes lockout
ATTEMPT_WINDOW_SECONDS = 60  # Time window for counting attempts

# In-memory store for login attempts
# Format: {ip: {"attempts": [(timestamp, success), ...], "locked_until": timestamp or None}}
LOGIN_ATTEMPTS: dict[str, dict[str, Any]] = {}


def cleanup_old_attempts() -> None:
    """Remove old login attempt records."""
    current_time = time.time()
    ips_to_remove = []

    for ip, data in LOGIN_ATTEMPTS.items():
        # Remove attempts older than the window
        data["attempts"] = [
            (ts, success)
            for ts, success in data["attempts"]
            if current_time - ts < ATTEMPT_WINDOW_SECONDS
        ]

        # Check if lockout expired
        if data.get("locked_until") and current_time > data["locked_until"]:
            data["locked_until"] = None
            data["attempts"] = []

        # Remove empty records
        if not data["attempts"] and not data.get("locked_until"):
            ips_to_remove.append(ip)

    for ip in ips_to_remove:
        del LOGIN_ATTEMPTS[ip]


def check_rate_limit(ip: str) -> tuple[bool, int]:
    """
    Check if IP is rate limited.
    Returns: (is_allowed, seconds_until_unlock)
    """
    cleanup_old_attempts()
    current_time = time.time()

    if ip not in LOGIN_ATTEMPTS:
        return True, 0

    data = LOGIN_ATTEMPTS[ip]

    # Check if locked out
    if data.get("locked_until"):
        if current_time < data["locked_until"]:
            return False, int(data["locked_until"] - current_time)
        else:
            # Lockout expired
            data["locked_until"] = None
            data["attempts"] = []
            return True, 0

    # Count failed attempts in the window
    failed_attempts = sum(
        1
        for ts, success in data["attempts"]
        if not success and current_time - ts < ATTEMPT_WINDOW_SECONDS
    )

    if failed_attempts >= MAX_LOGIN_ATTEMPTS:
        # Lock out the IP
        data["locked_until"] = current_time + LOCKOUT_SECONDS
        return False, LOCKOUT_SECONDS

    return True, 0


def record_login_attempt(ip: str, success: bool) -> None:
    """Record a login attempt."""
    current_time = time.time()

    if ip not in LOGIN_ATTEMPTS:
        LOGIN_ATTEMPTS[ip] = {"attempts": [], "locked_until": None}

    LOGIN_ATTEMPTS[ip]["attempts"].append((current_time, success))

    # If successful, clear failed attempts
    if success:
        LOGIN_ATTEMPTS[ip]["attempts"] = []
        LOGIN_ATTEMPTS[ip]["locked_until"] = None


class CodeResponse(BaseModel):
    """Response with generated code."""

    code: str
    expires_in: int


class CodeStatusResponse(BaseModel):
    """Response with code status."""

    status: str  # "pending", "verified", "expired"
    token: str | None = None
    user: dict[str, Any] | None = None


class VerifyCodeRequest(BaseModel):
    """Request to verify code from bot."""

    code: str
    telegram_id: int
    first_name: str
    last_name: str | None = None
    username: str | None = None


def cleanup_expired_codes() -> None:
    """Remove expired codes from memory."""
    current_time = time.time()
    expired = [
        code
        for code, data in AUTH_CODES.items()
        if current_time - data["created_at"] > CODE_EXPIRY_SECONDS
    ]
    for code in expired:
        del AUTH_CODES[code]


def create_admin_token(user_id: int, telegram_id: int | None) -> str:
    """Create JWT token for admin session."""
    payload = {
        "sub": str(user_id),
        "telegram_id": telegram_id,
        "type": "admin",
        "exp": int(time.time()) + (settings.access_token_expire_minutes * 60),
    }
    token: str = jwt.encode(
        payload,
        settings.secret_key.get_secret_value(),
        algorithm=settings.algorithm,
    )
    return token


@router.post("/code", response_model=CodeResponse)
async def generate_code() -> CodeResponse:
    """Generate a new auth code for admin login."""
    cleanup_expired_codes()

    # Generate 6-digit code
    code = secrets.token_hex(3).upper()  # 6 hex chars

    AUTH_CODES[code] = {
        "created_at": time.time(),
        "telegram_id": None,
        "user_data": None,
    }

    return CodeResponse(code=code, expires_in=CODE_EXPIRY_SECONDS)


@router.get("/code/{code}", response_model=CodeStatusResponse)
async def check_code_status(
    code: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CodeStatusResponse:
    """Check if code was verified by bot."""
    cleanup_expired_codes()

    code = code.upper()

    if code not in AUTH_CODES:
        return CodeStatusResponse(status="expired")

    code_data = AUTH_CODES[code]

    # Check if expired
    if time.time() - code_data["created_at"] > CODE_EXPIRY_SECONDS:
        del AUTH_CODES[code]
        return CodeStatusResponse(status="expired")

    # Check if verified
    if code_data["telegram_id"] is None:
        return CodeStatusResponse(status="pending")

    telegram_id = code_data["telegram_id"]
    result = await db.execute(
        select(User)
        .where(User.telegram_id == telegram_id)
        .execution_options(populate_existing=True)
    )
    user = result.scalar_one_or_none()
    if telegram_id not in ADMIN_TELEGRAM_IDS or not user or not user.is_admin or not user.is_active:
        del AUTH_CODES[code]
        raise HTTPException(status_code=403, detail="Access denied. You are not an admin.")
    token = create_admin_token(user.id, user.telegram_id)
    del AUTH_CODES[code]

    return CodeStatusResponse(
        status="verified",
        token=token,
        user={
            "id": user.id,
            "telegram_id": user.telegram_id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
        },
    )


@router.post("/verify")
async def verify_code(
    request: VerifyCodeRequest,
    x_bot_token: Annotated[str | None, Header(alias="X-Bot-Token")] = None,
) -> dict[str, Any]:
    """
    Verify auth code from bot.

    Called by the bot when user sends a code.
    Protected by X-Bot-Token header.
    """
    # Verify bot token
    if not x_bot_token or not _hmac.compare_digest(x_bot_token, settings.telegram_bot_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bot token",
        )

    cleanup_expired_codes()

    code = request.code.upper()

    if code not in AUTH_CODES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Code not found or expired",
        )

    code_data = AUTH_CODES[code]

    # Check if expired
    if time.time() - code_data["created_at"] > CODE_EXPIRY_SECONDS:
        del AUTH_CODES[code]
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Code expired",
        )

    # Check if already verified
    if code_data["telegram_id"] is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Code already used",
        )

    # Check if user is admin
    if request.telegram_id not in ADMIN_TELEGRAM_IDS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. You are not an admin.",
        )

    # Mark code as verified
    AUTH_CODES[code]["telegram_id"] = request.telegram_id
    AUTH_CODES[code]["user_data"] = {
        "first_name": request.first_name,
        "last_name": request.last_name,
        "username": request.username,
    }

    return {"status": "ok", "message": "Code verified successfully"}


@router.get("/me")
async def get_current_admin(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Get the currently authorized admin, without creating or granting an identity."""
    user = await require_admin_header(db, authorization)

    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
    }


# =============================================================================
# Simple password-based admin login (for admin panel)
# =============================================================================


class LoginRequest(BaseModel):
    """Simple login request."""

    model_config = ConfigDict(strict=True, extra="forbid")
    username: str = Field(max_length=128)
    password: SecretStr


class LoginResponse(BaseModel):
    """Login response with token."""

    access_token: str
    token_type: str = "bearer"
    user: dict[str, Any]


_verification_capacity = VerificationCapacity()


def _login_refused(client_ip: str) -> HTTPException:
    record_login_attempt(client_ip, success=False)
    return HTTPException(status_code=401, detail="Invalid username or password")


@router.post("/login", response_model=LoginResponse)
async def admin_login(
    request: LoginRequest,
    http_request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> LoginResponse:
    """Authenticate a named/compat credential; token issuance never grants a role."""
    # Audit S1: the buyer rule; X-Real-IP counts only from BUYER_TRUSTED_PROXY_PEERS.
    client_ip = peer(http_request, settings)
    is_allowed, wait_seconds = check_rate_limit(client_ip)
    if not is_allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many login attempts. Try again in {wait_seconds} seconds.",
            headers={"Retry-After": str(wait_seconds)},
        )
    try:
        login = normalize_login(request.username, allow_compat=True)
        value = request.password.get_secret_value()
        encoded = value.encode("utf-8") if login == COMPAT_LOGIN else password_bytes(value)
        if not encoded:
            raise ValueError("Invalid password")
    except ValueError:
        raise _login_refused(client_ip) from None

    try:
        with _verification_capacity.admit():
            if settings.database_echo or db.get_bind().engine.echo:
                raise PasswordServiceUnavailable()
            if login == COMPAT_LOGIN:
                configured = settings.admin_password.get_secret_value()
                user = await db.scalar(
                    select(User)
                    .where(User.telegram_id == settings.admin_telegram_id)
                    .execution_options(populate_existing=True)
                )
                matches = bool(configured) and _hmac.compare_digest(
                    encoded, configured.encode("utf-8")
                )
            else:
                account = await db.scalar(
                    select(AdminPasswordAccount)
                    .where(AdminPasswordAccount.login == login)
                    .execution_options(populate_existing=True)
                )
                matches = await check_hash(
                    encoded, account.password_hash if account else DUMMY_HASH
                )
                user = (
                    await db.scalar(
                        select(User)
                        .where(User.id == account.user_id)
                        .execution_options(populate_existing=True)
                    )
                    if account
                    else None
                )
            if not matches or not user or not user.is_admin or not user.is_active:
                raise _login_refused(client_ip)
            token = create_admin_token(user.id, user.telegram_id)
    except (PasswordServiceUnavailable, SQLAlchemyError):
        raise HTTPException(
            status_code=503, detail="Authentication temporarily unavailable"
        ) from None

    record_login_attempt(client_ip, success=True)
    return LoginResponse(
        access_token=token,
        user={
            "id": user.id,
            "telegram_id": user.telegram_id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "is_admin": True,
        },
    )
