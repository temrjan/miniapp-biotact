"""Guest order creation, status projection and payment start (G1, spec S2 sections 5-6).

The request session is ``get_db``'s, which commits only after the response is sent; every write
whose result a response announces is committed here explicitly, and every refusal after a flush or
an allocation rolls the request transaction back before the refusal is returned.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.buyer_auth.security import keyed
from src.config import Settings
from src.guest.schemas import GuestLine, GuestOrderRequest
from src.orders.models import Order, OrderItem, OrderStatus
from src.orders.schemas import OrderItemCreate, OrderQuote
from src.orders.service import (
    DELIVERY_FEE,
    ProductOutOfStockError,
    UnknownProductError,
    _price_lines,
    _quote_from_priced,
    generate_order_number,
)
from src.payments import size_stock

logger = logging.getLogger(__name__)

GUEST_COOKIE = "__Host-biotact_guest"
ACCESS_LIFETIME = timedelta(days=30)  # L-7
COOKIE_MAX_AGE = 2592000  # L-7, seconds
MAX_LINES = 20  # L-8
MAX_SIZED_UNITS = 10  # L-8
ORDER_PEER_LIMIT = ("guest-order-peer", 10, 3600)  # L-8, BD-05
SIZE_REFUSALS = frozenset(
    {"size_required", "invalid_size", "size_unavailable", "size_cutover_pending"}
)


class GuestRefusal(Exception):
    """A value-free refusal with its HTTP status, ``error`` code and the S2 extra fields."""

    def __init__(self, status: int, code: str, **extra: Any) -> None:
        self.status = status
        self.code = code
        self.extra = extra
        super().__init__(code)


@dataclass(frozen=True)
class Created:
    projection: dict[str, Any]


@dataclass(frozen=True)
class Replayed:
    order_number: str
    state: str


def too_large(lines: list[GuestLine]) -> bool:
    """OC-03: more than 20 lines, or more than 10 units over lines that name a size."""
    return (
        len(lines) > MAX_LINES
        or sum(line.quantity for line in lines if line.size is not None) > MAX_SIZED_UNITS
    )


def state(order: Order) -> str:
    """ST-02 projection, identical for sized and unsized orders."""
    if order.status == OrderStatus.CANCELLED or order.payment_status in ("failed", "refunded"):
        return "cancelled"
    if order.payment_status == "paid":
        return "paid"
    return "awaiting_payment"


def projection(order: Order, items: list[OrderItem], csrf_token: str) -> dict[str, Any]:
    """ST-01 body: exactly these keys, nothing about the buyer, the provider or the owner."""
    return {
        "order_number": order.order_number,
        "state": state(order),
        "lines": [
            {
                "product_id": item.product_id,
                "product_name": item.product_name,
                "size": item.size,
                "quantity": item.quantity,
                "unit_price": f"{item.product_price:.2f}",
                "line_total": f"{item.total:.2f}",
            }
            for item in items
        ],
        "total": f"{order.total:.2f}",
        "currency": order.currency,
        "csrf_token": csrf_token,
    }


def csrf_for(config: Settings, secret: str) -> str:
    return keyed(config, "guest-csrf", secret)


def stored_phone(value: str) -> str:
    """OC-05: an optional leading ``+`` followed by the digits of the validated number."""
    digits = "".join(c for c in value if c in "0123456789")
    return ("+" if value.startswith("+") else "") + digits


async def _priced(
    db: AsyncSession, lines: list[GuestLine], *, allocate: bool
) -> tuple[list[OrderItem], Decimal, OrderQuote]:
    requested = [
        OrderItemCreate(product_id=line.product_id, quantity=line.quantity, size=line.size)
        for line in lines
    ]
    try:
        items, subtotal, _ = await _price_lines(db, requested, allocate=allocate)
    except (UnknownProductError, ProductOutOfStockError) as exc:
        raise GuestRefusal(
            409, "items_unavailable", product_ids=sorted(set(exc.product_ids))
        ) from None
    except HTTPException as exc:
        detail: object = exc.detail
        if isinstance(detail, dict) and detail.get("code") in SIZE_REFUSALS:
            raise GuestRefusal(
                409, "items_unavailable", product_ids=[detail["product_id"]]
            ) from None
        raise
    return items, subtotal, _quote_from_priced(items, subtotal, balance=0, points=0)


async def quote(db: AsyncSession, lines: list[GuestLine]) -> OrderQuote:
    """OC-01: catalogue prices, no allocation, no loyalty read, no write."""
    return (await _priced(db, lines, allocate=False))[2]


async def find_by_idempotency(db: AsyncSession, digest: str) -> Order | None:
    return (
        await db.execute(select(Order).where(Order.guest_idempotency_digest == digest))
    ).scalar_one_or_none()


async def find_by_access(db: AsyncSession, digest: str, at: datetime) -> Order | None:
    """ST-01 lookup: the cookie's order while it is younger than 30 days."""
    return (
        await db.execute(
            select(Order)
            .options(selectinload(Order.items))
            .where(Order.guest_access_digest == digest, Order.created_at > at - ACCESS_LIFETIME)
        )
    ).scalar_one_or_none()


async def create_order(
    db: AsyncSession, config: Settings, body: GuestOrderRequest, secret: str
) -> Created | Replayed:
    """OC-04 to OC-07. The order row is inserted first, as in the Mini App path, so that a concurrent
    request with the same key waits on the unique index before taking any product or size lock."""
    at = datetime.now(UTC)
    idempotency = keyed(config, "guest-idempotency", body.idempotency_key)
    existing = await find_by_idempotency(db, idempotency)
    if existing is not None:
        return Replayed(existing.order_number, state(existing))
    order = Order(
        order_number=generate_order_number(),
        user_id=None,
        subtotal=Decimal("0"),
        delivery_fee=DELIVERY_FEE,
        total=Decimal("0"),
        points_applied=0,
        delivery_name=body.name,
        delivery_phone=stored_phone(body.phone),
        delivery_address=body.address,
        customer_note=None,
        legal_terms_version=config.guest_terms_version,
        legal_privacy_version=config.guest_privacy_version,
        legal_accepted_at=at,
        guest_access_digest=keyed(config, "guest-access", secret),
        guest_idempotency_digest=idempotency,
        items=[],
    )
    db.add(order)
    try:
        await db.flush()
    except IntegrityError:
        # A same-key winner committed while this insert waited on its unique index.
        await db.rollback()
        existing = await find_by_idempotency(db, idempotency)
        if existing is None:
            raise
        return Replayed(existing.order_number, state(existing))
    try:
        items, subtotal, current = await _priced(db, body.items, allocate=True)
    except GuestRefusal:
        await db.rollback()
        raise
    if current.quote_fingerprint != body.quote_fingerprint:
        await db.rollback()
        raise GuestRefusal(409, "quote_changed", quote=current.model_dump(mode="json"))
    order.subtotal = subtotal
    order.total = subtotal + DELIVERY_FEE
    if any(item.size_id is not None for item in items):
        order.size_stock_protocol = size_stock.PROTOCOL
        order.size_owner_state = "not_started"
        order.accepted_settlement = False
    order.items.extend(items)
    await db.flush()
    body_out = projection(order, items, csrf_for(config, secret))
    await db.commit()
    return Created(body_out)
