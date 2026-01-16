"""Analytics API router."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, HTTPException, status, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from jose import jwt, JWTError

from src.config import settings
from src.database import get_db
from src.users.models import User
from src.analytics import service
from src.analytics.schemas import DashboardStats

router = APIRouter()

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def require_admin(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Dependency to require admin authentication."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
        )

    token = authorization.replace("Bearer ", "")

    try:
        payload = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            algorithms=[settings.algorithm],
        )
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    if payload.get("type") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not an admin token",
        )

    user_id = int(payload.get("sub", 0))
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()

    if not user or not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    return user


@router.get("/dashboard", response_model=DashboardStats, dependencies=[Depends(require_admin)])
async def get_dashboard(
    db: DbSession,
    days: int = Query(30, ge=1, le=365, description="Number of days for revenue chart"),
) -> DashboardStats:
    """Get dashboard statistics (admin only)."""
    return await service.get_dashboard_stats(db, days=days)
