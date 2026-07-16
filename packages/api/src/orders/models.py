"""Order SQLAlchemy models."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, Text, func
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
    __table_args__ = (
        # Model-1: the reserve subquery filters (user_id, payment_status IN ('unpaid','pending')) on
        # every partial redemption. A composite index keeps it index-only as order volume grows; the
        # standalone ``user_id`` index (below) stays for the order-list queries. Non-unique.
        Index("ix_orders_user_id_payment_status", "user_id", "payment_status"),
    )

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

    # Biotact Club points applied to this order (1 балл = 1 сум). 0 ⇔ pure card payment.
    # Points spent leave NO trace outside this database — no invoice, no fiscal receipt, no bank
    # statement for the points part — so this column is part of the accounting record itself, which
    # is why the fact is stored and not inferred.
    # Since partial redemption (Stage 6, Captain 2026-07-15): ``points_applied > 0`` means points
    # were spent, NOT that the order is paid. Full coverage (points == total) is settled outright
    # at creation; a partial order stays ``unpaid`` until Multicard collects the remainder. The sole
    # source of "paid" is ``payment_status == 'paid'``.
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
