"""Order schemas."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_serializer


# Order Item schemas
class OrderItemBase(BaseModel):
    """Base order item schema."""

    product_id: int | None = None
    product_name: str
    product_price: Decimal
    quantity: int = Field(ge=1)
    total: Decimal
    size: str | None = None


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
    size: str | None = Field(None, max_length=20, strict=True)


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

    ``points_to_apply`` is how many Biotact Club points to spend on this order — the amount, never
    a price: ``0`` (default — today's Multicard path, unchanged; older clients that do not send the
    field land here) is card-only; ``= subtotal`` covers the goods in full (settled outright,
    Multicard never involved); anything between debits that many points now and invoices only the
    remainder to Multicard (Stage 6 partial redemption). The amount is validated server-side against
    the redeem rules — see :func:`src.orders.service.max_redeemable`.
    """

    items: list[OrderItemCreate] = Field(min_length=1)
    # Points to spend on this order (the amount, not a price). 0 = card-only. The bounds — min 500,
    # multiples of 100, card remainder exactly 0 or ≥ 5000 — are enforced in the service, not here:
    # they depend on the server-priced subtotal and the caller's live balance, which a schema
    # cannot see. ``ge=0`` only rejects a negative request outright.
    points_to_apply: int = Field(0, ge=0)
    quote_fingerprint: str | None = Field(None, max_length=64)
    referral_token: StrictStr | None = Field(None, repr=False)
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
    # Points applied to this order (0 ⇔ pure card payment) — the mini-app success/history screens
    # and the admin read it from here. Since partial redemption (Stage 6) ``> 0`` no longer means
    # "paid": it means points were spent; whether the order is settled is ``payment_status``.
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


class SizeStockLine(BaseModel):
    order_item_id: int
    size: str
    allocated_quantity: int
    restored_quantity: int


class SizeStockDetails(BaseModel):
    protocol: str
    owner_state: str
    revision: int
    dispatched_at: datetime | None
    lines: list[SizeStockLine]


class AdminOrderResponse(OrderResponse):
    size_stock: SizeStockDetails | None = None


class AdminOrderListResponse(BaseModel):
    items: list[AdminOrderResponse]
    total: int
    page: int
    size: int
    pages: int


class SizeReturnLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_item_id: StrictInt
    quantity: Annotated[StrictInt, Field(gt=0, le=2147483647)]


class SizeReturnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    reason: str = Field(min_length=1, max_length=2000)
    items: list[SizeReturnLine] = Field(min_length=1)


class SizeReturnResponse(BaseModel):
    revision: int
    lines: list[SizeStockLine]


class ReleaseReservationsResponse(BaseModel):
    """Result of the customer releasing the points their unfinished orders were holding.

    Reports a partial outcome honestly instead of collapsing it into ok/error: ``blocked_orders``
    counts reservations whose Multicard invoice refused deletion (the case where cancelling would
    risk a late charge), and those points are still held. A response with ``released_points == 0``
    and ``blocked_orders == 0`` simply means there was nothing to release — pressing the button
    twice is not an error.
    """

    released_orders: int = Field(ge=0, description="How many reserving orders were cancelled")
    released_points: int = Field(ge=0, description="How many points became spendable again")
    blocked_orders: int = Field(
        ge=0, description="Reservations that could not be released and still hold their points"
    )


class OrderStatusUpdate(BaseModel):
    """Update order status schema."""

    status: str = Field(..., pattern="^(pending|confirmed|processing|shipped|delivered|cancelled)$")


class QuoteLine(BaseModel):
    product_id: int
    product_name: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal
    size: str | None = None

    @field_serializer("unit_price", "line_total")
    def serialize_money(self, value: Decimal) -> str:
        return f"{value:.2f}"


class OrderQuote(BaseModel):
    lines: list[QuoteLine]
    subtotal: Decimal
    delivery_fee: Decimal
    total: Decimal
    max_redeemable: int
    points_to_apply: int
    quote_fingerprint: str

    @field_serializer("subtotal", "delivery_fee", "total")
    def serialize_money(self, value: Decimal) -> str:
        return f"{value:.2f}"
