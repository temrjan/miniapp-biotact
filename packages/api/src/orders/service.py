"""Order service layer."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.orders.models import Order, OrderItem, OrderStatus
from src.orders.schemas import OrderCreate, OrderItemCreate, OrderListResponse
from src.products.models import Product

# Delivery is free today and the server is the single source of that truth — the client used to
# declare it. There is no delivery-pricing rule anywhere in the project to read it from.
DELIVERY_FEE = Decimal("0")


class OrderValidationError(Exception):
    """The requested lines cannot become an order — mapped to 422 by the router."""


class UnknownProductError(OrderValidationError):
    """One or more ``product_id`` values do not exist in the catalogue."""

    def __init__(self, product_ids: list[int]) -> None:
        self.product_ids = product_ids
        super().__init__(f"Unknown product(s): {product_ids}")


class ProductOutOfStockError(OrderValidationError):
    """One or more requested products are flagged ``is_in_stock = false``."""

    def __init__(self, product_ids: list[int]) -> None:
        self.product_ids = product_ids
        super().__init__(f"Product(s) out of stock: {product_ids}")


def generate_order_number() -> str:
    """Generate unique order number."""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    unique_id = uuid.uuid4().hex[:6].upper()
    return f"ORD-{timestamp}-{unique_id}"


async def get_order_by_id(
    db: AsyncSession,
    order_id: int,
) -> Order | None:
    """Get order by ID."""
    result = await db.execute(select(Order).where(Order.id == order_id))
    return result.scalar_one_or_none()


async def get_order_with_items(
    db: AsyncSession,
    order_id: int,
) -> Order | None:
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
) -> Order | None:
    """Get order by order number."""
    result = await db.execute(select(Order).where(Order.order_number == order_number))
    return result.scalar_one_or_none()


async def get_order_by_payment_uuid(
    db: AsyncSession,
    payment_uuid: str,
) -> Order | None:
    """Get order by payment UUID."""
    result = await db.execute(select(Order).where(Order.payment_uuid == payment_uuid))
    return result.scalar_one_or_none()


async def _price_lines(
    db: AsyncSession,
    lines: list[OrderItemCreate],
) -> tuple[list[OrderItem], Decimal]:
    """Turn requested (product_id, quantity) lines into priced ``OrderItem`` rows + the subtotal.

    Every money value is read from ``products`` in a single query — the client's numbers never
    reach the database, so a tampered ``product_price`` cannot become a Multicard invoice. Name and
    price are snapshotted onto the line, so a later catalogue edit does not rewrite past orders.
    """
    product_ids = {line.product_id for line in lines}
    products = (
        (await db.execute(select(Product).where(Product.id.in_(product_ids)))).scalars().all()
    )
    by_id = {product.id: product for product in products}

    missing = sorted(product_ids - by_id.keys())
    if missing:
        raise UnknownProductError(missing)

    out_of_stock = sorted(pid for pid in product_ids if not by_id[pid].is_in_stock)
    if out_of_stock:
        raise ProductOutOfStockError(out_of_stock)

    items: list[OrderItem] = []
    subtotal = Decimal("0")
    for line in lines:
        product = by_id[line.product_id]
        line_total = product.price * line.quantity
        subtotal += line_total
        items.append(
            OrderItem(
                product_id=product.id,
                product_name=product.name,
                product_price=product.price,
                quantity=line.quantity,
                total=line_total,
            )
        )
    return items, subtotal


async def create_order(
    db: AsyncSession,
    order_data: OrderCreate,
    user_id: int,
) -> Order:
    """Create an order priced by the SERVER; the client supplies identity and quantity only.

    Raises :class:`OrderValidationError` (unknown product / out of stock) — the router maps it to
    422.
    """
    items, subtotal = await _price_lines(db, order_data.items)

    order = Order(
        order_number=generate_order_number(),
        user_id=user_id,
        subtotal=subtotal,
        delivery_fee=DELIVERY_FEE,
        total=subtotal + DELIVERY_FEE,
        delivery_address=order_data.delivery_address,
        delivery_phone=order_data.delivery_phone,
        delivery_name=order_data.delivery_name,
        customer_note=order_data.customer_note,
    )
    order.items.extend(items)

    db.add(order)
    await db.flush()
    await db.refresh(order)
    return order


async def update_order_status(
    db: AsyncSession,
    order_id: int,
    status: str,
) -> Order | None:
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
) -> Order | None:
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
        query.options(selectinload(Order.items))
        .order_by(Order.created_at.desc())
        .offset(offset)
        .limit(size)
    )
    items = list(result.scalars().all())

    # Calculate pages
    pages = (total + size - 1) // size if total > 0 else 1

    return OrderListResponse(
        items=items,  # type: ignore[arg-type]
        total=total,
        page=page,
        size=size,
        pages=pages,
    )
