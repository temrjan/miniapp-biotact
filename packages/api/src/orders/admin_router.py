"""Admin Orders API router."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.auth.dependencies import require_admin, verify_admin_jwt
from src.database import get_db
from src.orders import service
from src.orders.dependencies import authorize_order_status
from src.orders.models import Order, OrderStatus
from src.orders.schemas import (
    AdminOrderListResponse,
    AdminOrderResponse,
    OrderStatusUpdate,
    SizeReturnRequest,
    SizeReturnResponse,
    SizeStockLine,
)
from src.payments.size_stock import PROTOCOL, conflict, lock_order, record
from src.products.stock import restore_allocations
from src.users.models import User

router = APIRouter()

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_model=AdminOrderListResponse, dependencies=[Depends(verify_admin_jwt)])
async def get_all_orders(
    db: DbSession,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    search: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    payment_status: str | None = Query(None),
) -> AdminOrderListResponse:
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

    return AdminOrderListResponse(
        items=orders,  # type: ignore[arg-type]
        total=total or 0,
        page=page,
        size=size,
        pages=pages,
    )


@router.get(
    "/{order_id}", response_model=AdminOrderResponse, dependencies=[Depends(verify_admin_jwt)]
)
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
    "/{order_id}/status",
    response_model=AdminOrderResponse,
    dependencies=[Depends(authorize_order_status)],
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

    updated = await service.update_order_status(db, order_id, status_data.status)
    assert updated is not None
    await db.commit()
    return updated


@router.post("/{order_id}/size-returns")
async def confirm_size_return(
    order_id: int,
    data: SizeReturnRequest,
    db: DbSession,
    admin: Annotated[User, Depends(require_admin)],
) -> SizeReturnResponse:
    order = await lock_order(db, order_id)
    if order.size_stock_protocol != PROTOCOL or order.dispatched_at is None:
        raise HTTPException(status_code=422, detail="A dispatched sized order is required")
    if order.size_revision != data.expected_revision:
        raise conflict("order_state_conflict")
    quantities = {entry.order_item_id: entry.quantity for entry in data.items}
    eligible = {item.id for item in order.items if item.size_id is not None}
    if len(quantities) != len(data.items) or not quantities.keys() <= eligible:
        raise HTTPException(status_code=422, detail="Supply each returned sized line once")
    await restore_allocations(db, order, quantities)
    order.size_revision += 1
    lines = [
        SizeStockLine(
            order_item_id=item.id,
            size=item.size or "",
            allocated_quantity=item.allocated_quantity,
            restored_quantity=item.restored_quantity,
        )
        for item in order.items
        if item.id in quantities
    ]
    record(
        db,
        order,
        "physical_return",
        actor=f"admin:{admin.id}",
        reason=data.reason,
        evidence_ref=f"admin-return:order:{order.id}:revision:{data.expected_revision}",
        expected_revision=data.expected_revision,
        result={"revision": order.size_revision, "lines": [line.model_dump() for line in lines]},
    )
    response = SizeReturnResponse(revision=order.size_revision, lines=lines)
    await db.commit()
    return response
