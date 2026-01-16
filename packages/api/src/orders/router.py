"""Order API router."""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query, Header
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_db
from src.orders import service
from src.orders.schemas import (
    OrderCreate,
    OrderResponse,
    OrderListResponse,
    OrderStatusUpdate,
)
from src.auth.dependencies import CurrentUser, get_current_user
from src.auth.telegram import validate_init_data, InitDataValidationError
from src.auth.service import get_or_create_user
from src.config import settings

router = APIRouter()

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_optional_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    x_init_data: Annotated[Optional[str], Header(alias="X-Init-Data")] = None,
):
    """Get user if auth provided, otherwise return None."""
    if not x_init_data or not settings.telegram_bot_token:
        return None
    try:
        init_data = validate_init_data(x_init_data, settings.telegram_bot_token)
        if init_data.user:
            return await get_or_create_user(db, init_data.user)
    except InitDataValidationError:
        pass
    return None


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    order_data: OrderCreate,
    db: DbSession,
    user=Depends(get_optional_user),
):
    """Create new order."""
    user_id = user.id if user else 1
    return await service.create_order(db, order_data, user_id=user_id)


@router.get("", response_model=OrderListResponse)
async def get_orders(
    db: DbSession,
    user=Depends(get_optional_user),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    """Get user orders."""
    user_id = user.id if user else 1
    return await service.get_user_orders(db, user_id=user_id, page=page, size=size)


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: int,
    db: DbSession,
    user=Depends(get_optional_user),
):
    """Get order by ID."""
    order = await service.get_order_with_items(db, order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )
    if user and order.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not your order",
        )
    return order


@router.patch("/{order_id}/status", response_model=OrderResponse)
async def update_order_status(
    order_id: int,
    status_data: OrderStatusUpdate,
    db: DbSession,
):
    """Update order status (admin only)."""
    order = await service.update_order_status(db, order_id, status_data.status)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )
    return order
