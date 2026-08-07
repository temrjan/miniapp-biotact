"""Order service layer."""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.loyalty import service as loyalty_service
from src.orders.models import Order, OrderItem, OrderStatus
from src.orders.schemas import OrderCreate, OrderItemCreate, OrderListResponse
from src.payments.service import multicard_client
from src.products.models import Product

logger = logging.getLogger(__name__)

# Delivery is free today and the server is the single source of that truth — the client used to
# declare it. There is no delivery-pricing rule anywhere in the project to read it from.
DELIVERY_FEE = Decimal("0")

# Redeem rules (Captain-ratified 2026-07-14/15). Points cover the goods (subtotal) only, never
# delivery. A redemption is at least MIN_REDEEM, floored to REDEEM_STEP, and must leave the card
# either nothing to collect or at least MIN_CARD_REMAINDER — the 1..MIN_CARD_REMAINDER-1 band is
# forbidden: it fences off Multicard's undocumented minimum invoice without relying on their
# (unresponsive) support.
MIN_REDEEM = 500
REDEEM_STEP = 100
MIN_CARD_REMAINDER = 5000
_REDEEM_STEP_DECIMAL = Decimal(REDEEM_STEP)


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


class InvalidRedeemAmountError(OrderValidationError):
    """``points_to_apply`` breaks a redeem rule — a malformed amount (422), not a balance shortfall.

    Distinct from :class:`~src.loyalty.service.InsufficientPointsError` (a live-balance 409): this
    is an amount the caller could have formed correctly — below the minimum, off the step, over the
    ceiling, or leaving the card in the forbidden 1..MIN_CARD_REMAINDER-1 band. Carries the computed
    :func:`max_redeemable` so the mini-app can clamp its stepper without a second round-trip.
    """

    def __init__(self, *, points: int, max_redeemable: int) -> None:
        self.points = points
        self.max_redeemable = max_redeemable
        super().__init__(
            f"invalid redeem amount {points}: expected a multiple of {REDEEM_STEP} in "
            f"[{MIN_REDEEM}, {max_redeemable}] leaving the card 0 or ≥ {MIN_CARD_REMAINDER}"
        )


def _floor_to_step(value: Decimal) -> int:
    """Largest multiple of :data:`REDEEM_STEP` not exceeding ``value`` (i.e. rounded down)."""
    return int((Decimal(value) // _REDEEM_STEP_DECIMAL) * _REDEEM_STEP_DECIMAL)


def max_redeemable(*, balance: int, subtotal: Decimal, total: Decimal) -> int:
    """Largest point amount applicable to an order — ``0`` when redemption is unavailable.

    Only the goods are redeemable (``eligible = subtotal``); delivery is never paid with points.
    Two shapes qualify and the larger wins:

    * **Full cover** — spend exactly ``subtotal`` (only when it is itself a whole multiple of
      :data:`REDEEM_STEP`, else no such legal amount exists) if the balance affords it and the card
      is left either nothing (delivery-free today) or at least :data:`MIN_CARD_REMAINDER`.
    * **Partial** — the largest step-aligned amount leaving the card at least
      :data:`MIN_CARD_REMAINDER`, capped by both the balance and ``eligible``.

    Pure function of three numbers: cheap to unit-test, and the exact ceiling the request validator
    and the mini-app stepper both clamp to.
    """
    eligible = subtotal
    candidates: list[int] = []

    if balance >= eligible and eligible == _floor_to_step(eligible):
        card_remainder = total - eligible
        if card_remainder == 0 or card_remainder >= MIN_CARD_REMAINDER:
            candidates.append(int(eligible))

    partial = _floor_to_step(min(Decimal(balance), eligible, total - MIN_CARD_REMAINDER))
    if partial >= MIN_REDEEM:
        candidates.append(partial)

    return max(candidates, default=0)


def _validate_redeem_amount(
    points: int, *, balance: int, subtotal: Decimal, total: Decimal
) -> None:
    """Reject a ``points_to_apply`` that breaks any redeem rule (min / step / ceiling / dead-zone).

    A form-level UX filter reading the balance WITHOUT a lock. The authoritative balance check
    stays inside :func:`~src.loyalty.service.reserve_points` under ``FOR UPDATE``; if a concurrent
    order drains the balance after this passes, that check refuses with a 409 and the balance never
    goes negative. The dead-zone (card remainder in ``1..MIN_CARD_REMAINDER-1``) is checked
    explicitly — it is not implied by ``points <= max_redeemable`` for an arbitrary amount.
    """
    max_allowed = max_redeemable(balance=balance, subtotal=subtotal, total=total)
    card_remainder = total - points
    if (
        points < MIN_REDEEM
        or points % REDEEM_STEP != 0
        or points > max_allowed
        or (card_remainder != 0 and card_remainder < MIN_CARD_REMAINDER)
    ):
        raise InvalidRedeemAmountError(points=points, max_redeemable=max_allowed)


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

    ``points_to_apply == 0`` (the default) is today's card path, byte-for-byte: the order is
    created ``pending``/``unpaid`` and money moves later via Multicard.

    ``points_to_apply > 0`` spends that many Biotact Club points now — validated against the redeem
    rules first (malformed amount → 422) — inside this one request (= one transaction: ``get_db``
    commits at request end, any raise rolls everything back), so the redeem debit and the order row
    live or die together. Full coverage (``points == total``) settles the order outright — ``paid``
    + ``confirmed``, Multicard never contacted. A partial redemption keeps the order ``unpaid`` /
    ``pending``: the points are debited now and the card is invoiced for the remainder later.

    Raises :class:`OrderValidationError` (unknown product / out of stock / invalid redeem amount)
    — the router maps it to 422 — and :class:`~src.loyalty.service.InsufficientPointsError` —
    mapped to 409, nothing persisted.
    """
    items, subtotal = await _price_lines(db, order_data.items)
    total = subtotal + DELIVERY_FEE

    points = order_data.points_to_apply
    if points > 0:
        # Form-level UX guard on an UNLOCKED balance read; reserve_points re-checks the same balance
        # under FOR UPDATE, so a concurrent drain becomes a truthful 409, never a negative balance.
        balance = await loyalty_service.get_balance(db, user_id)
        _validate_redeem_amount(points, balance=balance, subtotal=subtotal, total=total)

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
    is_full = points > 0 and total - points == 0
    if points > 0:
        order.points_applied = points
        if is_full:  # full coverage — settled outright, no Multicard leg to wait for
            order.payment_status = "paid"
            order.status = OrderStatus.CONFIRMED
            order.paid_at = datetime.now(UTC)
        # A partial redemption keeps the default unpaid/pending — the pending order row itself is
        # the reservation; the card collects the remainder and the debit lands only on settlement.
    order.items.extend(items)

    db.add(order)
    await db.flush()
    if points > 0:
        # After the flush so the reservation sum can exclude this order by id. Admission is
        # reserve-aware under the customer_profile lock (Model-1): a PARTIAL reserves (no ledger row
        # now — an abandoned card leg cannot burn points), a full-coverage order settles the debit
        # atomically. A shortfall against the available balance rolls the whole request back (409);
        # the flushed-but-uncommitted order never outlives a refusal.
        await loyalty_service.reserve_points(
            db, user_id=user_id, order_id=order.id, amount=points, settle_now=is_full
        )
    await db.refresh(order)
    return order


# Model-1 reserve timeout: how long a partial redemption may pin its reserved points before the next
# attempt reclaims it. A single card payment completes in minutes; 30 minutes is a generous ceiling
# that never cuts off a customer still paying (Captain-ratified default, 2026-07-16). The value is
# not money-critical — with no ledger row there is nothing to burn at any timeout: too short only
# risks cancelling an order a customer is mid-paying (UX, not money), too long holds the reserve.
REDEEM_RESERVE_TIMEOUT = timedelta(minutes=30)

# Пауза между вызовами к Multicard в регулярной уборке. Накопившаяся очередь уходит
# залпом: 07.08.2026 уборка 68 заказов с диагностикой упёрлась в 429 на /auth.
SWEEP_CALL_INTERVAL_SECONDS = 0.2


@dataclass(frozen=True)
class ReleaseOutcome:
    """What one reservation-release pass actually managed to do.

    ``blocked_orders`` is not an error count — it is the orders whose Multicard invoice refused to be
    deleted, which is precisely the case where cancelling would be dangerous. It is reported so the
    caller can tell "nothing was freed" from "some was freed" instead of collapsing both into a
    failure: a customer whose points partly came back must not be told the whole thing failed.
    """

    released_orders: int
    released_points: int
    blocked_orders: int


async def expire_stale_partials(
    db: AsyncSession,
    *,
    user_id: int,
    max_age: timedelta | None = REDEEM_RESERVE_TIMEOUT,
) -> ReleaseOutcome:
    """Reclaim this user's partial-redemption reservations (Model-1) — lazily, or on demand.

    A partial redemption reserves points by keeping an unpaid order alive with no ledger row. If the
    customer abandons the card leg the reservation would pin those points forever — Multicard sends
    NO webhook on an abandoned invoice, so nothing else ever frees it. Reclaiming means: delete the
    Multicard invoice FIRST, cancel the order only on a confirmed deletion (see
    :func:`_expire_partial`). The caller commits this as its own unit so the external DELETE is never
    orphaned by a later rollback.

    ``max_age`` decides WHICH reservations qualify, and it is the only difference between the two
    callers:

    * the default :data:`REDEEM_RESERVE_TIMEOUT` is the lazy cleanup that runs before a new
      redemption — it waits, because it cannot know whether the customer is still mid-payment;
    * ``None`` drops the age bar entirely and is used by the customer-triggered release ("вернуть
      баллы"): the customer has just said they are not paying, so there is nothing to wait for.

    Dropping the age bar is safe for the same reason the lazy path is safe — safety lives in the
    delete-then-cancel order, not in the waiting. An invoice that is actually paid refuses deletion
    and its order is left untouched.

    Only THIS user's reservations are touched — a reserve is per-user (it reduces only its owner's
    available balance), so a customer who never returns harms no one else; a periodic sweep for those
    stragglers is deliberately out of scope (no scheduler exists) and tracked separately.
    """
    filters = [
        Order.user_id == user_id,
        Order.payment_status.in_(("unpaid", "pending")),
        Order.points_applied > 0,
    ]
    if max_age is not None:
        filters.append(Order.created_at < datetime.now(UTC) - max_age)
    stale = (await db.execute(select(Order).where(*filters))).scalars().all()

    released_orders = released_points = blocked_orders = 0
    for order in stale:
        # Read the amount BEFORE cancelling: after the update+commit the instance can be expired,
        # and re-reading it then would either refetch or raise instead of yielding the figure we
        # need to report back to the customer.
        points = order.points_applied
        if await _expire_partial(db, order):
            released_orders += 1
            released_points += points
        else:
            blocked_orders += 1
    return ReleaseOutcome(
        released_orders=released_orders,
        released_points=released_points,
        blocked_orders=blocked_orders,
    )


# ⚠️ ЗАПУСК ОБЯЗАН БЫТЬ ЧАСТЫМ — часы, не сутки. Счёт Multicard живёт около 24 часов, а
# `_expire_partial` отменяет заказ ТОЛЬКО на подтверждённом удалении счёта. Заказ, созданный сразу
# после суточного прогона, дойдёт до следующего уже с мёртвым счётом — и останется неотменяемым
# навсегда. Частота здесь — вопрос корректности, а не опрятности.
async def expire_stale_orders(
    db: AsyncSession,
    *,
    max_age: timedelta = REDEEM_RESERVE_TIMEOUT,
) -> ReleaseOutcome:
    """Reclaim abandoned orders of EVERY payment kind, across all customers — the periodic sweep.

    Why this exists next to :func:`expire_stale_partials` instead of relaxing its filter: that one is
    reactive and per-customer (it runs before a new redemption and behind the «вернуть баллы»
    button, the latter with ``max_age=None``). Dropping its ``points_applied > 0`` filter would make
    the button cancel a pure-card order the customer is paying AT THAT MOMENT. The two have
    different triggers and different safety envelopes, so they stay separate functions over the same
    primitive.

    Card-only abandoned orders had no reclaimer at all: Multicard sends no webhook for an abandoned
    invoice, and the reactive path skips them (`orders/router.py`: `if points_to_apply > 0`). They
    sat in `PENDING` forever, which the customer reads as "the money is about to be taken".

    Run frequency is a correctness constraint, not housekeeping — see the comment above.
    """
    filters = [
        Order.status == OrderStatus.PENDING,
        Order.payment_status.in_(("unpaid", "pending")),
        Order.created_at < datetime.now(UTC) - max_age,
    ]
    stale = (await db.execute(select(Order).where(*filters))).scalars().all()

    released_orders = released_points = blocked_orders = 0
    for index, order in enumerate(stale):
        # Пауза между заказами: каждый — отдельный вызов к Multicard, и накопившаяся очередь
        # уходит в них залпом. 07.08.2026 уборка 68 заказов вместе с диагностикой упёрлась в
        # `429 Too Many Requests` на их `/auth` — это измеренный факт, а не осторожность.
        # Отказ по 429 безопасен сам по себе (`delete_invoice` вернёт False, заказ не отменится и
        # попадёт в следующий прогон), но тогда прогон тратит вызовы впустую.
        if index:
            await asyncio.sleep(SWEEP_CALL_INTERVAL_SECONDS)
        points = order.points_applied
        if await _expire_partial(db, order):
            released_orders += 1
            released_points += points
        else:
            blocked_orders += 1
    return ReleaseOutcome(
        released_orders=released_orders,
        released_points=released_points,
        blocked_orders=blocked_orders,
    )


async def _expire_partial(db: AsyncSession, order: Order) -> bool:
    """Expire ONE stale partial: delete its Multicard invoice, then cancel — in that order, safely.

    Order of operations is load-bearing (M3, the third burn path): the invoice is deleted FIRST and
    the order is cancelled ONLY on a confirmed deletion. ``delete_invoice`` refuses an already-paid
    invoice (and returns False on any transport error); cancelling then would abandon an order
    Multicard still considers payable — a late authorisation would charge the card for a cancelled
    order. So on anything but a confirmed delete we leave the order untouched (its points never
    burned — there is no ledger row) and let the normal success callback settle it if the money did
    arrive. The cancel is an atomic conditional UPDATE (mirrors :func:`try_arm_payment_attempt` and
    the ``activate_code`` claim): a no-op if a real success settled the order meanwhile. An order that
    never reached ``/payments/create`` has no ``payment_uuid`` and no invoice to delete, so it is
    cancelled directly. Returns whether the order was cancelled.
    """
    if order.payment_uuid is not None:
        deleted = await multicard_client.delete_invoice(order.payment_uuid)
        if not deleted:
            logger.warning(
                "stale partial %s: invoice %s not deleted (paid/refused/transport) — leaving as-is",
                order.order_number,
                order.payment_uuid,
            )
            return False
    cancelled = (
        await db.execute(
            update(Order)
            .where(Order.id == order.id, Order.payment_status.in_(("unpaid", "pending")))
            .values(payment_status="failed", status=OrderStatus.CANCELLED)
            .returning(Order.id)
        )
    ).scalar_one_or_none()
    return cancelled is not None


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
        order.completed_at = datetime.now(UTC)

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
        order.paid_at = datetime.now(UTC)

    await db.flush()
    await db.refresh(order)
    return order


async def try_arm_payment_attempt(
    db: AsyncSession,
    order_id: int,
    payment_uuid: str,
) -> bool:
    """Record a new Multicard attempt on the order — ONLY if it is not already paid.

    Atomic ``UPDATE … WHERE payment_status != 'paid'`` closes the create-payment TOCTOU: the
    caller checks ownership/paid-state, then calls Multicard (up to 30s), then arms the attempt.
    A concurrent success callback can land ``paid`` inside that window; a blind write would then
    stamp the order back to ``pending`` over a real payment. Guarding the write on the same
    condition makes it a no-op instead — the row simply is not matched. Returns whether a row was
    armed (``False`` ⇒ already paid, caller must not proceed).
    """
    armed = (
        await db.execute(
            update(Order)
            .where(Order.id == order_id, Order.payment_status != "paid")
            .values(payment_uuid=payment_uuid, payment_status="pending")
            .returning(Order.id)
        )
    ).scalar_one_or_none()
    return armed is not None


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
