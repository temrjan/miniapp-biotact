"""Order schemas."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


# Order Item schemas
class OrderItemBase(BaseModel):
    """Base order item schema."""
    product_id: int
    product_name: str
    product_price: Decimal
    quantity: int = Field(ge=1)
    total: Decimal


class OrderItemCreate(OrderItemBase):
    """Create order item schema."""
    pass


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


class OrderCreate(OrderBase):
    """Create order schema."""
    items: list[OrderItemCreate]


class OrderResponse(OrderBase):
    """Order response schema."""
    id: int
    order_number: str
    user_id: int
    status: str
    payment_uuid: str | None = None
    payment_status: str | None = None
    paid_at: datetime | None = None
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
