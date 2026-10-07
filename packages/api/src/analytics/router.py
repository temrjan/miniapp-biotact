"""Analytics API router."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from src.analytics import service
from src.analytics.schemas import DashboardStats, LoyaltyStats
from src.auth.dependencies import require_admin_header
from src.database import get_db
from src.users.models import User

router = APIRouter()

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def require_admin(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Delegate to the shared current-account rule, preserving this router's signature."""
    return await require_admin_header(db, authorization)


@router.get("/dashboard", response_model=DashboardStats, dependencies=[Depends(require_admin)])
async def get_dashboard(
    db: DbSession,
    days: int = Query(30, ge=1, le=365, description="Number of days for revenue chart"),
) -> DashboardStats:
    """Get dashboard statistics (admin only)."""
    return await service.get_dashboard_stats(db, days=days)


@router.get("/loyalty", response_model=LoyaltyStats, dependencies=[Depends(require_admin)])
async def get_loyalty(
    db: DbSession,
    days: int = Query(30, ge=1, le=365, description="Number of days for the activation trend"),
) -> LoyaltyStats:
    """Get Biotact Club loyalty monitoring statistics (admin only)."""
    return await service.get_loyalty_stats(db, days=days)
