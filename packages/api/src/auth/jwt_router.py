import secrets
from datetime import datetime, timedelta

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.email_service import email_service
from src.auth.models import AdminUser
from src.config import settings
from src.database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer()

# Token expiration
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7


# Schemas
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    first_name: str | None = None
    last_name: str | None = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = ACCESS_TOKEN_EXPIRE_MINUTES * 60


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: int
    email: str
    first_name: str | None
    last_name: str | None
    is_verified: bool
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class MessageResponse(BaseModel):
    message: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


# Utils
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))


def create_access_token(user_id: int) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {"sub": str(user_id), "type": "access", "exp": expire}
    return jwt.encode(to_encode, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: int) -> str:
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode = {"sub": str(user_id), "type": "refresh", "exp": expire}
    return jwt.encode(to_encode, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm)


def generate_token() -> str:
    return secrets.token_urlsafe(32)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    session: AsyncSession = Depends(get_db)
) -> AdminUser:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.jwt_secret.get_secret_value(), algorithms=[settings.jwt_algorithm])
        user_id = int(payload.get("sub"))
        token_type = payload.get("type")
        if user_id is None or token_type != "access":
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid token") from exc

    result = await session.execute(select(AdminUser).where(AdminUser.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=401, detail="User is disabled")

    return user


# Endpoints
@router.post("/register", response_model=MessageResponse)
async def register(data: RegisterRequest, session: AsyncSession = Depends(get_db)) -> MessageResponse:
    result = await session.execute(select(AdminUser).where(AdminUser.email == data.email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Email already registered")

    verification_token = generate_token()
    user = AdminUser(
        email=data.email,
        password_hash=hash_password(data.password),
        first_name=data.first_name,
        last_name=data.last_name,
        is_verified=False,
        verification_token=verification_token,
        verification_token_expires=datetime.utcnow() + timedelta(hours=24),
    )
    session.add(user)
    await session.commit()

    email_service.send_verification_email(data.email, verification_token, data.first_name)
    return {"message": "Registration successful. Please check your email to verify your account."}


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, session: AsyncSession = Depends(get_db)) -> TokenResponse:
    result = await session.execute(select(AdminUser).where(AdminUser.email == data.email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_verified:
        raise HTTPException(status_code=401, detail="Email not verified. Please check your email.")

    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account is disabled")

    user.last_login = datetime.utcnow()
    await session.commit()

    return {
        "access_token": create_access_token(user.id),
        "refresh_token": create_refresh_token(user.id),
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60
    }


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(data: RefreshRequest, session: AsyncSession = Depends(get_db)) -> TokenResponse:
    try:
        payload = jwt.decode(data.refresh_token, settings.jwt_secret.get_secret_value(), algorithms=[settings.jwt_algorithm])
        user_id = int(payload.get("sub"))
        token_type = payload.get("type")

        if token_type != "refresh":
            raise HTTPException(status_code=401, detail="Invalid refresh token")
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token") from exc

    result = await session.execute(select(AdminUser).where(AdminUser.id == user_id))
    user = result.scalar_one_or_none()

    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or disabled")

    return {
        "access_token": create_access_token(user.id),
        "refresh_token": create_refresh_token(user.id),
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60
    }


@router.get("/verify-email", response_model=MessageResponse)
async def verify_email(token: str, session: AsyncSession = Depends(get_db)) -> MessageResponse:
    result = await session.execute(
        select(AdminUser).where(
            AdminUser.verification_token == token,
            AdminUser.verification_token_expires > datetime.utcnow()
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")

    user.is_verified = True
    user.verification_token = None
    user.verification_token_expires = None
    await session.commit()

    return {"message": "Email verified successfully. You can now login."}


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: AdminUser = Depends(get_current_user)) -> AdminUser:
    return current_user


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(data: ForgotPasswordRequest, session: AsyncSession = Depends(get_db)) -> MessageResponse:
    result = await session.execute(select(AdminUser).where(AdminUser.email == data.email))
    user = result.scalar_one_or_none()

    if user:
        reset_token = generate_token()
        user.reset_token = reset_token
        user.reset_token_expires = datetime.utcnow() + timedelta(hours=1)
        await session.commit()
        email_service.send_password_reset_email(data.email, reset_token, user.first_name)

    return {"message": "If this email exists, a password reset link has been sent."}


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(data: ResetPasswordRequest, session: AsyncSession = Depends(get_db)) -> MessageResponse:
    result = await session.execute(
        select(AdminUser).where(
            AdminUser.reset_token == data.token,
            AdminUser.reset_token_expires > datetime.utcnow()
        )
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user.password_hash = hash_password(data.password)
    user.reset_token = None
    user.reset_token_expires = None
    await session.commit()

    return {"message": "Password reset successfully. You can now login."}
