"""Order API router."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user, verify_admin_jwt
from src.common.exceptions import ForbiddenException
from src.database import get_db
from src.orders import service
from src.orders.models import Order
from src.orders.schemas import (
    OrderCreate,
    OrderListResponse,
    OrderResponse,
    OrderStatusUpdate,
)
from src.users.models import User

router = APIRouter()

DbSession = Annotated[AsyncSession, Depends(get_db)]
# Orders are per-person data on the money path: identity is required, never optional. The previous
# optional dependency silently fell back to ``user_id = 1``, which is what made every order
# readable and creatable without any auth at all.
CurrentUser = Annotated[User, Depends(get_current_user)]


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    order_data: OrderCreate,
    db: DbSession,
    user: CurrentUser,
) -> Order:
    """Create a new order. Prices come from the catalogue — the request carries no money."""
    try:
        return await service.create_order(db, order_data, user_id=user.id)
    except service.OrderValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc


@router.get("", response_model=OrderListResponse)
async def get_orders(
    db: DbSession,
    user: CurrentUser,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
) -> OrderListResponse:
    """Get the caller's own orders."""
    return await service.get_user_orders(db, user_id=user.id, page=page, size=size)


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: int,
    db: DbSession,
    user: CurrentUser,
) -> Order:
    """Get one of the caller's own orders.

    The ownership check is unconditional. It used to be guarded by ``if user and ...``, so an
    anonymous caller skipped it entirely and could read anyone's order — address, phone, name and
    contents included.
    """
    order = await service.get_order_with_items(db, order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )
    if order.user_id != user.id:
        raise ForbiddenException("Not your order")
    return order


@router.patch(
    "/{order_id}/status", response_model=OrderResponse, dependencies=[Depends(verify_admin_jwt)]
)
async def update_order_status(
    order_id: int,
    status_data: OrderStatusUpdate,
    db: DbSession,
) -> Order:
    """Update order status (admin only)."""
    order = await service.update_order_status(db, order_id, status_data.status)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )
    return order
