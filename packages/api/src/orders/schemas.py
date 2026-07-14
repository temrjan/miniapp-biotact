"""Order schemas."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


# Order Item schemas
class OrderItemBase(BaseModel):
    """Base order item schema."""

    product_id: int | None = None
    product_name: str
    product_price: Decimal
    quantity: int = Field(ge=1)
    total: Decimal


class OrderItemCreate(BaseModel):
    """One requested line — identity and quantity ONLY; the money comes from the catalogue.

    Deliberately NOT derived from :class:`OrderItemBase`: prices are never taken from the client
    (:func:`src.orders.service.create_order` prices every line from ``products``). Pydantic ignores
    unknown fields, so an older mini-app build that still posts ``product_name``/``product_price``/
    ``total`` keeps working through the deploy window — those values are simply dropped.
    """

    product_id: int
    # Upper bound guards against an absurd invoice (quantity=1_000_000 → a billion-sum charge).
    # It is NOT stock control: ``stock_quantity`` is not enforced anywhere yet.
    quantity: int = Field(ge=1, le=99)


class OrderItemResponse(OrderItemBase):
    """Order item response schema."""

    id: int

    class Config:
        from_attributes = True


# Order schemas
class OrderBase(BaseModel):
    """Base order schema."""

    subtotal: Decimal
    delivery_fee: Decimal | None = Decimal("0")
    total: Decimal
    delivery_address: str | None = None
    delivery_phone: str | None = None
    delivery_name: str | None = None
    customer_note: str | None = None


class OrderCreate(BaseModel):
    """Create-order request: delivery details + requested lines.

    Carries NO money. ``subtotal``/``delivery_fee``/``total`` are computed by the server from the
    catalogue — a client-declared total is exactly the hole this schema closes.

    ``payment_method`` selects HOW the order is paid, never how much: ``card`` (default —
    today's Multicard path, unchanged; older clients that do not send the field land here) or
    ``points`` (Stage 6 — the whole total is debited from the Biotact Club balance,
    all-or-nothing, Multicard never involved).
    """

    items: list[OrderItemCreate] = Field(min_length=1)
    payment_method: Literal["card", "points"] = "card"
    delivery_address: str | None = None
    delivery_phone: str | None = None
    delivery_name: str | None = None
    customer_note: str | None = None


class OrderResponse(OrderBase):
    """Order response schema."""

    id: int
    order_number: str
    user_id: int
    status: str
    payment_uuid: str | None = None
    payment_status: str | None = None
    paid_at: datetime | None = None
    # Points that paid this order (0 ⇔ paid with money) — the mini-app success/history screens
    # and the admin read it from here; all-or-nothing, so >0 means "paid with points" outright.
    points_applied: int = 0
    currency: str = "UZS"
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    items: list[OrderItemResponse] = []

    class Config:
        from_attributes = True


class OrderListResponse(BaseModel):
    """Paginated order list response."""

    items: list[OrderResponse]
    total: int
    page: int
    size: int
    pages: int


class OrderStatusUpdate(BaseModel):
    """Update order status schema."""

    status: str = Field(..., pattern="^(pending|confirmed|processing|shipped|delivered|cancelled)$")
