"""Order schemas."""

from datetime import datetime
from decimal import Decimal
from typing import Optional

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
    delivery_fee: Optional[Decimal] = Decimal("0")
    total: Decimal
    delivery_address: Optional[str] = None
    delivery_phone: Optional[str] = None
    delivery_name: Optional[str] = None
    customer_note: Optional[str] = None


class OrderCreate(OrderBase):
    """Create order schema."""
    items: list[OrderItemCreate]


class OrderResponse(OrderBase):
    """Order response schema."""
    id: int
    order_number: str
    user_id: int
    status: str
    payment_uuid: Optional[str] = None
    payment_status: Optional[str] = None
    paid_at: Optional[datetime] = None
    currency: str = "UZS"
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
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
