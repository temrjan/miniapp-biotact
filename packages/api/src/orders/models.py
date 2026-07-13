"""Order SQLAlchemy models."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base

if TYPE_CHECKING:
    from src.products.models import Product


class OrderStatus(StrEnum):
    """Order status enum."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    PROCESSING = "processing"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class Order(Base):
    """Order model."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_number: Mapped[str] = mapped_column(String(50), unique=True, index=True)

    # User
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )

    # Status
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus),
        default=OrderStatus.PENDING,
    )

    # Payment (Multicard)
    payment_uuid: Mapped[str | None] = mapped_column(String(100), index=True)
    payment_status: Mapped[str | None] = mapped_column(String(20), default="unpaid")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Totals
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    delivery_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="UZS")

    # Biotact Club points that paid for this order (1 балл = 1 сум). 0 ⇔ paid with money.
    # An order paid with points never reaches Multicard, so it leaves NO trace outside this
    # database — no invoice, no fiscal receipt, no bank statement. This column is therefore the
    # accounting record itself, which is why the fact lives in the schema and is not inferred.
    # All-or-nothing by design (Captain, 2026-07-13): partial point payment does not exist, so
    # ``points_applied > 0`` unambiguously means "paid with points" — no second source of truth.
    points_applied: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0
    )

    # Delivery info
    delivery_address: Mapped[str | None] = mapped_column(Text)
    delivery_phone: Mapped[str | None] = mapped_column(String(20))
    delivery_name: Mapped[str | None] = mapped_column(String(200))

    # Notes
    customer_note: Mapped[str | None] = mapped_column(Text)
    admin_note: Mapped[str | None] = mapped_column(Text)

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Order(id={self.id}, number={self.order_number})>"


class OrderItem(Base):
    """Order item model."""

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"),
        index=True,
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Snapshot of product at time of order
    product_name: Mapped[str] = mapped_column(String(200))
    product_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2))

    # Relationships
    order: Mapped["Order"] = relationship(back_populates="items")
    product: Mapped[Optional["Product"]] = relationship(lazy="joined")

    def __repr__(self) -> str:
        return f"<OrderItem(id={self.id}, product={self.product_name})>"
