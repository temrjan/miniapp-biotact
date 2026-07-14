"""Order service layer."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.loyalty import service as loyalty_service
from src.orders.models import Order, OrderItem, OrderStatus
from src.orders.schemas import OrderCreate, OrderItemCreate, OrderListResponse
from src.products.models import Product

# Delivery is free today and the server is the single source of that truth — the client used to
# declare it. There is no delivery-pricing rule anywhere in the project to read it from.
DELIVERY_FEE = Decimal("0")


class OrderValidationError(Exception):
    """The requested lines cannot become an order — mapped to 422 by the router."""


class UnknownProductError(OrderValidationError):
    """One or more ``product_id`` values are not on sale: absent, or delisted (``is_active=false``).

    Delisted is deliberately not a distinct error: the storefront hides such products, so from a
    buyer's point of view they simply do not exist — and saying "this delisted product exists"
    would leak the catalogue's private state.
    """

    def __init__(self, product_ids: list[int]) -> None:
        self.product_ids = product_ids
        super().__init__(f"Unknown product(s): {product_ids}")


class ProductOutOfStockError(OrderValidationError):
    """One or more requested products are flagged ``is_in_stock = false``."""

    def __init__(self, product_ids: list[int]) -> None:
        self.product_ids = product_ids
        super().__init__(f"Product(s) out of stock: {product_ids}")


class TotalNotPayableWithPointsError(OrderValidationError):
    """The order total is not a positive whole number of sums — points cannot pay it.

    Points are whole integers (1 балл = 1 сум) and a points order leaves no trace outside this
    database, so the DB is the accounting record itself: a fractional total is REFUSED loudly,
    never silently rounded (the same stance ``get_unit_price`` takes with FractionalPriceError
    on the earn side). A non-positive total is refused for the same reason the card path
    refuses it at invoice time — except here "paid" would be stamped with no invoice to catch
    it. Both are unreachable from the storefront today (catalogue prices validate ``gt=0`` and
    whole); this guards against out-of-band catalogue data, not a client path.
    """

    def __init__(self, total: Decimal) -> None:
        self.total = total
        super().__init__(f"Order total {total} cannot be paid with points")


def _points_total(total: Decimal) -> int:
    """The exact whole-sum point amount for ``total``, or a loud refusal — never a rounding."""
    if total <= 0 or total != total.to_integral_value():
        raise TotalNotPayableWithPointsError(total)
    return int(total)


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
        (
            await db.execute(
                select(Product).where(
                    Product.id.in_(product_ids),
                    # ``is_active`` is how an admin takes a product off sale — the storefront filters
                    # on exactly this flag (products/service.py). Without it here, a delisted product
                    # stays orderable by its (sequential, guessable) id and still earns a real
                    # Multicard invoice and fiscal receipt. Delisted reads as "does not exist".
                    Product.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
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

    ``payment_method='card'`` (the default) is today's path, byte-for-byte: the order is
    created ``pending``/``unpaid`` and money moves later via Multicard.

    ``payment_method='points'`` pays the WHOLE total from the Biotact Club balance inside this
    one request (= one transaction — ``get_db`` commits at request end, any raise rolls
    everything back): the redeem debit and the order row live or die together. On success the
    order is immediately ``paid`` + ``confirmed`` (there is no gateway to wait for); Multicard
    is never contacted, so this row is the only accounting trace the purchase has.

    Raises :class:`OrderValidationError` (unknown product / out of stock / total not payable
    with points) — the router maps it to 422 — and
    :class:`~src.loyalty.service.InsufficientPointsError` — mapped to 409, nothing persisted.
    """
    items, subtotal = await _price_lines(db, order_data.items)
    total = subtotal + DELIVERY_FEE

    pay_with_points = order_data.payment_method == "points"
    points_to_apply = _points_total(total) if pay_with_points else 0

    order = Order(
        order_number=generate_order_number(),
        user_id=user_id,
        subtotal=subtotal,
        delivery_fee=DELIVERY_FEE,
        total=total,
        delivery_address=order_data.delivery_address,
        delivery_phone=order_data.delivery_phone,
        delivery_name=order_data.delivery_name,
        customer_note=order_data.customer_note,
    )
    if pay_with_points:
        order.points_applied = points_to_apply
        order.payment_status = "paid"
        order.status = OrderStatus.CONFIRMED
        order.paid_at = datetime.utcnow()
    order.items.extend(items)

    db.add(order)
    await db.flush()
    if pay_with_points:
        # After the flush so the ledger row can carry this order's id; the balance check and
        # the debit serialize on the customer_profile row lock inside redeem_points, so the
        # flushed-but-uncommitted order above cannot outlive a refusal (409 → rollback).
        await loyalty_service.redeem_points(
            db, user_id=user_id, order_id=order.id, amount=points_to_apply
        )
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
