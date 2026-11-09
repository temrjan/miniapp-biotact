"""Order service layer."""

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Select, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from src.employee_referrals.capture import capture_order, capture_requested
from src.employee_referrals.paid import record_paid
from src.event_promotions.service import ActivePromotion, active_promotion, promotion_unit_price
from src.loyalty import service as loyalty_service
from src.orders.models import Order, OrderItem, OrderStatus
from src.orders.schemas import (
    OrderCreate,
    OrderItemCreate,
    OrderListResponse,
    OrderQuote,
    QuoteLine,
)
from src.payments.service import multicard_client
from src.products.models import Product
from src.products.stock import lock_products, lock_sizes, size_error

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
    *,
    allocate: bool = False,
    promotion: ActivePromotion | None = None,
) -> tuple[list[OrderItem], Decimal, bool]:
    """Turn requested (product_id, quantity) lines into priced ``OrderItem`` rows + the subtotal.

    Every money value is read from ``products`` in a single query — the client's numbers never
    reach the database, so a tampered ``product_price`` cannot become a Multicard invoice. Name and
    price are snapshotted onto the line, so a later catalogue edit does not rewrite past orders.

    ``promotion`` is the buyer's selected event grant (event-qr-discount R-10): each line costs the
    lower of its catalogue effective price and the promotion unit price, and records the difference
    in ``promotion_discount`` only when the promotion price is strictly lower. The website guest
    path never passes one (R-11).
    """
    product_ids = {line.product_id for line in lines}
    products = (
        (
            await db.execute(
                select(Product)
                .options(joinedload(Product.category))
                .where(
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
    managed_ids = {pid for pid, product in by_id.items() if product.requires_size or pid == 39}
    if allocate:
        # Ordinary orders keep their existing locking behavior. Vest 39 also needs
        # the product lock before initialization, so a cutover cannot miss an admission.
        by_id.update(await lock_products(db, managed_ids))
        by_id = {pid: product for pid, product in by_id.items() if product.is_active}
        products = list(by_id.values())

    missing = sorted(product_ids - by_id.keys())
    if missing:
        raise UnknownProductError(missing)

    out_of_stock = sorted(pid for pid in product_ids if not by_id[pid].is_in_stock)
    if out_of_stock:
        raise ProductOutOfStockError(out_of_stock)

    sizes = (
        await lock_sizes(db, managed_ids)
        if allocate
        else {row.id: row for product in products for row in product.sizes}
    )
    grouped: dict[tuple[int, str], OrderItem] = {}
    items: list[OrderItem] = []
    subtotal = Decimal("0")
    for line in lines:
        product = by_id[line.product_id]
        if product.size_cutover_blocked:
            raise size_error("size_cutover_pending", product.id, line.size, 409)
        size_row = None
        if product.requires_size:
            if line.size is None:
                raise size_error("size_required", product.id, None)
            size_row = next(
                (
                    row
                    for row in sizes.values()
                    if row.product_id == product.id and row.size == line.size
                ),
                None,
            )
            if size_row is None:
                raise size_error("invalid_size", product.id, line.size)
            if product.category is not None and not product.category.is_purchasable:
                raise size_error("size_unavailable", product.id, line.size, 409)
        elif line.size is not None:
            raise size_error("invalid_size", product.id, line.size)
        price = product.sale_price if product.is_sale else product.price
        assert price is not None  # products_sale_ck guarantees a price for active sales.
        discount = Decimal("0")
        if promotion is not None:
            promoted = promotion_unit_price(product.price, promotion.percent)
            if promoted < price:
                discount = (price - promoted) * line.quantity
                price = promoted
        line_total = price * line.quantity
        subtotal += line_total
        if size_row is not None and (product.id, size_row.size) in grouped:
            item = grouped[(product.id, size_row.size)]
            item.quantity += line.quantity
            item.total += line_total
            item.promotion_discount += discount
            continue
        item = OrderItem(
            product_id=product.id,
            product_name=product.name,
            product_price=price,
            is_sale_at_order=product.is_sale,
            regular_price_at_order=product.price,
            quantity=line.quantity,
            total=line_total,
            size=line.size,
            size_id=size_row.id if size_row is not None else None,
            allocated_quantity=0,
            restored_quantity=0,
            promotion_discount=discount,
        )
        items.append(item)
        if size_row is not None:
            grouped[(product.id, size_row.size)] = item
    for item in grouped.values():
        assert item.size_id is not None
        assert item.product_id is not None
        row = sizes[item.size_id]
        if row.available_quantity < item.quantity:
            raise size_error("size_unavailable", item.product_id, item.size, 409)
        if allocate:
            row.available_quantity -= item.quantity
            row.revision += 1
            item.allocated_quantity = item.quantity
    return items, subtotal, any(product.is_sale for product in products)


class QuoteAgreementError(Exception):
    def __init__(self, code: str, quote: OrderQuote) -> None:
        self.code = code
        self.quote = quote
        super().__init__(code)


def _promotion_discount(items: list[OrderItem]) -> Decimal:
    return sum((item.promotion_discount for item in items), Decimal("0"))


def _quote_from_priced(
    items: list[OrderItem],
    subtotal: Decimal,
    *,
    balance: int,
    points: int,
    promotion: ActivePromotion | None = None,
) -> OrderQuote:
    total = subtotal + DELIVERY_FEE
    # The fingerprint covers the event discount through each line's unit price and total (R-13):
    # a grant that ends between quote and order changes them. Quotes without a discounted line
    # keep their base fingerprint.
    discount = _promotion_discount(items)
    lines = []
    for item in items:
        assert item.product_id is not None  # Newly priced lines all originate in the catalogue.
        lines.append(
            QuoteLine(
                product_id=item.product_id,
                product_name=item.product_name,
                quantity=item.quantity,
                unit_price=item.product_price,
                line_total=item.total,
                size=item.size,
            )
        )
    ceiling = max_redeemable(balance=balance, subtotal=subtotal, total=total)
    canonical = {
        "lines": sorted(
            (
                line.model_dump(mode="json", exclude={"product_name"}, exclude_none=True)
                for line in lines
            ),
            key=lambda line: (line["product_id"], line["quantity"], line.get("size", "")),
        ),
        "subtotal": f"{subtotal:.2f}",
        "delivery_fee": f"{DELIVERY_FEE:.2f}",
        "total": f"{total:.2f}",
        "max_redeemable": ceiling,
        "points_to_apply": points,
    }
    fingerprint = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return OrderQuote(
        lines=lines,
        subtotal=subtotal,
        delivery_fee=DELIVERY_FEE,
        total=total,
        max_redeemable=ceiling,
        points_to_apply=points,
        quote_fingerprint=fingerprint,
        promotion_discount=discount if promotion is not None else None,
        promotion_percent=promotion.percent if promotion is not None else None,
        promotion_ends_at=promotion.ends_at if promotion is not None else None,
    )


async def build_quote(db: AsyncSession, order_data: OrderCreate, user_id: int) -> OrderQuote:
    promotion = await active_promotion(db, user_id)
    items, subtotal, _ = await _price_lines(db, order_data.items, promotion=promotion)
    balance = await loyalty_service.get_quote_balance(db, user_id)
    if order_data.points_to_apply > 0:
        _validate_redeem_amount(
            order_data.points_to_apply,
            balance=balance,
            subtotal=subtotal,
            total=subtotal + DELIVERY_FEE,
        )
    return _quote_from_priced(
        items, subtotal, balance=balance, points=order_data.points_to_apply, promotion=promotion
    )


async def create_order(
    db: AsyncSession,
    order_data: OrderCreate,
    user_id: int,
) -> Order:
    """Create an order priced by the SERVER; the client supplies identity and quantity only.

    ``points_to_apply == 0`` (the default) is today's card path, byte-for-byte: the order is
    created ``pending``/``unpaid`` and money moves later via Multicard.

    Full coverage (``points == total``) debits points and settles the order outright — ``paid``
    + ``confirmed``, Multicard never contacted. A partial redemption reserves points and keeps
    the order ``pending``/``unpaid`` until verified card settlement debits the reservation.
    Admission validates the redeem rules and balance in the same transaction as the order and
    any size allocation; a refusal rolls back every admission effect.

    Raises :class:`OrderValidationError` (unknown product / out of stock / invalid redeem amount)
    — the router maps it to 422 — and :class:`~src.loyalty.service.InsufficientPointsError` —
    mapped to 409, nothing persisted.
    """
    # Refuse unsafe SQL echo before any order or referral SQL.
    capture_requested(order_data.referral_token)
    # Establish the order identity before acquiring product/size/profile locks.
    order = Order(
        order_number=generate_order_number(),
        user_id=user_id,
        subtotal=Decimal("0"),
        delivery_fee=DELIVERY_FEE,
        total=Decimal("0"),
        delivery_address=order_data.delivery_address,
        delivery_phone=order_data.delivery_phone,
        delivery_name=order_data.delivery_name,
        customer_note=order_data.customer_note,
        items=[],
    )
    db.add(order)
    await db.flush()
    promotion = await active_promotion(db, user_id)
    items, subtotal, has_sale = await _price_lines(
        db, order_data.items, allocate=True, promotion=promotion
    )
    total = subtotal + DELIVERY_FEE
    # A promotion-discounted line needs the buyer's agreement exactly as a sale line does (R-13).
    discounted = _promotion_discount(items) > 0

    points = order_data.points_to_apply
    balance = 0
    if points > 0 or order_data.quote_fingerprint is not None or has_sale or discounted:
        balance = await loyalty_service.get_quote_balance(db, user_id)
    if order_data.quote_fingerprint is not None or has_sale or discounted:
        quote = _quote_from_priced(
            items, subtotal, balance=balance, points=points, promotion=promotion
        )
        if order_data.quote_fingerprint is None:
            raise QuoteAgreementError("quote_required", quote)
        if order_data.quote_fingerprint != quote.quote_fingerprint:
            raise QuoteAgreementError("quote_mismatch", quote)
    if points > 0:
        _validate_redeem_amount(points, balance=balance, subtotal=subtotal, total=total)

    order.subtotal = subtotal
    order.total = total
    if discounted:
        assert promotion is not None  # Only a selected grant discounts a line.
        order.promotion_id = promotion.promotion_id  # R-12
    is_full = points > 0 and total - points == 0
    if any(item.size_id is not None for item in items):
        order.size_stock_protocol = "size_stock_v1"
        order.size_owner_state = "paid" if is_full else "not_started"
        order.accepted_settlement = is_full
    if points > 0:
        order.points_applied = points
        # A partial redemption keeps the default unpaid/pending — the pending order row itself is
        # the reservation; the card collects the remainder and the debit lands only on settlement.
    order.items.extend(items)

    db.add(order)
    await db.flush()
    await capture_order(db, order, order_data.referral_token)
    if points > 0:
        # After the flush so the reservation sum can exclude this order by id. Admission is
        # reserve-aware under the customer_profile lock (Model-1): a PARTIAL reserves (no ledger row
        # now — an abandoned card leg cannot burn points), a full-coverage order settles the debit
        # atomically. A shortfall against the available balance rolls the whole request back (409);
        # the flushed-but-uncommitted order never outlives a refusal.
        await loyalty_service.reserve_points(
            db, user_id=user_id, order_id=order.id, amount=points, settle_now=is_full
        )
    if is_full:
        # Capture and real debit precede the paid transition in this same transaction.
        order.payment_status = "paid"
        order.status = OrderStatus.CONFIRMED
        order.paid_at = datetime.now(UTC)
        await db.flush()
        await record_paid(db, order)
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
        or_(
            and_(
                Order.size_stock_protocol.is_(None), Order.payment_status.in_(("unpaid", "pending"))
            ),
            loyalty_service.marked_reservation(),
        ),
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
    stale = (await db.execute(stale_orders_query(max_age))).scalars().all()

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


def stale_orders_query(max_age: timedelta) -> Select[tuple[Order]]:
    """The same read-only selection for dry-run and apply, including all held marked orders."""
    return select(Order).where(
        or_(
            and_(
                Order.size_stock_protocol.is_(None),
                Order.status == OrderStatus.PENDING,
                Order.payment_status.in_(("unpaid", "pending")),
            ),
            loyalty_service.marked_reservation(),
        ),
        Order.created_at < datetime.now(UTC) - max_age,
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
    if order.size_stock_protocol == "size_stock_v1":
        from src.payments.size_stock import cancel_owned_order

        return await cancel_owned_order(db, order.id)
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

    if order.size_stock_protocol == "size_stock_v1":
        from src.payments.size_stock import change_status

        return await change_status(db, order_id, OrderStatus(status))

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
