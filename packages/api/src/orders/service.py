"""Order service layer."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.orders.models import Order, OrderItem, OrderStatus
from src.orders.schemas import OrderCreate, OrderListResponse


def generate_order_number() -> str:
    """Generate unique order number."""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    unique_id = uuid.uuid4().hex[:6].upper()
    return f"ORD-{timestamp}-{unique_id}"


async def get_order_by_id(
    db: AsyncSession,
    order_id: int,
) -> Optional[Order]:
    """Get order by ID."""
    result = await db.execute(
        select(Order).where(Order.id == order_id)
    )
    return result.scalar_one_or_none()


async def get_order_with_items(
    db: AsyncSession,
    order_id: int,
) -> Optional[Order]:
    """Get order with items loaded."""
    result = await db.execute(
        select(Order)
        .options(selectinload(Order.items).selectinload(OrderItem.product))
        .where(Order.id == order_id)
    )
    return result.scalar_one_or_none()


async def get_order_by_number(
    db: AsyncSession,
    order_number: str,
) -> Optional[Order]:
    """Get order by order number."""
    result = await db.execute(
        select(Order).where(Order.order_number == order_number)
    )
    return result.scalar_one_or_none()


async def get_order_by_payment_uuid(
    db: AsyncSession,
    payment_uuid: str,
) -> Optional[Order]:
    """Get order by payment UUID."""
    result = await db.execute(
        select(Order).where(Order.payment_uuid == payment_uuid)
    )
    return result.scalar_one_or_none()


async def create_order(
    db: AsyncSession,
    order_data: OrderCreate,
    user_id: int,
) -> Order:
    """Create new order."""
    order = Order(
        order_number=generate_order_number(),
        user_id=user_id,
        subtotal=order_data.subtotal,
        delivery_fee=order_data.delivery_fee or Decimal("0"),
        total=order_data.total,
        delivery_address=order_data.delivery_address,
        delivery_phone=order_data.delivery_phone,
        delivery_name=order_data.delivery_name,
        customer_note=order_data.customer_note,
    )

    # Add order items
    for item_data in order_data.items:
        item = OrderItem(
            product_id=item_data.product_id,
            product_name=item_data.product_name,
            product_price=item_data.product_price,
            quantity=item_data.quantity,
            total=item_data.total,
        )
        order.items.append(item)

    db.add(order)
    await db.flush()
    await db.refresh(order)
    return order


async def update_order_status(
    db: AsyncSession,
    order_id: int,
    status: str,
) -> Optional[Order]:
    """Update order status."""
    order = await get_order_by_id(db, order_id)
    if not order:
        return None

    order.status = OrderStatus(status)

    if status == "delivered":
        order.completed_at = datetime.utcnow()

    await db.flush()
    await db.refresh(order)
    return order


async def update_order_payment(
    db: AsyncSession,
    order_id: int,
    payment_uuid: str,
    payment_status: str,
) -> Optional[Order]:
    """Update order payment info."""
    order = await get_order_by_id(db, order_id)
    if not order:
        return None

    order.payment_uuid = payment_uuid
    order.payment_status = payment_status

    if payment_status == "paid":
        order.paid_at = datetime.utcnow()

    await db.flush()
    await db.refresh(order)
    return order


async def get_user_orders(
    db: AsyncSession,
    user_id: int,
    page: int = 1,
    size: int = 20,
) -> OrderListResponse:
    """Get paginated list of user orders."""
    query = select(Order).where(Order.user_id == user_id)
    count_query = select(func.count(Order.id)).where(Order.user_id == user_id)

    # Count total
    count_result = await db.execute(count_query)
    total = count_result.scalar_one()

    # Get items
    offset = (page - 1) * size
    result = await db.execute(
        query
        .options(selectinload(Order.items))
        .order_by(Order.created_at.desc())
        .offset(offset)
        .limit(size)
    )
    items = list(result.scalars().all())

    # Calculate pages
    pages = (total + size - 1) // size if total > 0 else 1

    return OrderListResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=pages,
    )
