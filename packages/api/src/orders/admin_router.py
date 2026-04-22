"""Admin Orders API router."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.auth.dependencies import verify_admin_jwt
from src.database import get_db
from src.orders.models import Order, OrderStatus
from src.orders.schemas import OrderListResponse, OrderResponse, OrderStatusUpdate

router = APIRouter()

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=OrderListResponse, dependencies=[Depends(verify_admin_jwt)])
async def get_all_orders(
    db: DbSession,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    search: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    payment_status: str | None = Query(None),
) -> OrderListResponse:
    """Get all orders with filtering and pagination (admin only)."""

    # Base query
    query = select(Order).options(selectinload(Order.items))
    count_query = select(func.count(Order.id))

    # Apply filters
    if status_filter:
        try:
            order_status = OrderStatus(status_filter)
            query = query.where(Order.status == order_status)
            count_query = count_query.where(Order.status == order_status)
        except ValueError:
            pass  # Invalid status, ignore filter

    if search:
        search_filter = or_(
            Order.order_number.ilike(f"%{search}%"),
            Order.delivery_name.ilike(f"%{search}%"),
            Order.delivery_phone.ilike(f"%{search}%"),
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    if date_from:
        query = query.where(Order.created_at >= date_from)
        count_query = count_query.where(Order.created_at >= date_from)

    if date_to:
        query = query.where(Order.created_at <= date_to)
        count_query = count_query.where(Order.created_at <= date_to)

    if payment_status:
        query = query.where(Order.payment_status == payment_status)
        count_query = count_query.where(Order.payment_status == payment_status)

    # Get total count
    total = await db.scalar(count_query)

    # Apply pagination and ordering
    query = query.order_by(Order.created_at.desc())
    query = query.offset((page - 1) * size).limit(size)

    # Execute query
    result = await db.execute(query)
    orders = result.scalars().all()

    # Calculate pages
    pages = (total + size - 1) // size if total else 0

    return OrderListResponse(
        items=orders,  # type: ignore[arg-type]
        total=total or 0,
        page=page,
        size=size,
        pages=pages,
    )


@router.get("/{order_id}", response_model=OrderResponse, dependencies=[Depends(verify_admin_jwt)])
async def get_order_details(
    order_id: int,
    db: DbSession,
) -> Order:
    """Get order details by ID (admin only)."""
    query = select(Order).options(selectinload(Order.items)).where(Order.id == order_id)

    result = await db.execute(query)
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )

    return order


@router.patch(
    "/{order_id}/status", response_model=OrderResponse, dependencies=[Depends(verify_admin_jwt)]
)
async def update_order_status_admin(
    order_id: int,
    status_data: OrderStatusUpdate,
    db: DbSession,
) -> Order:
    """Update order status (admin only)."""
    query = select(Order).options(selectinload(Order.items)).where(Order.id == order_id)

    result = await db.execute(query)
    order = result.scalar_one_or_none()

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )

    # Update status
    try:
        new_status = OrderStatus(status_data.status)
        order.status = new_status

        # Set completed_at if delivered
        if new_status == OrderStatus.DELIVERED:
            order.completed_at = datetime.now()

        await db.commit()
        await db.refresh(order)

    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status: {status_data.status}",
        ) from exc

    return order
