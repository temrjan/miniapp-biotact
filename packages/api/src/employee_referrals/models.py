"""Additive replica, permanent identities and durable receipt storage."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


class CampaignReplica(Base):
    __tablename__ = "employee_referral_campaigns"
    __table_args__ = (
        PrimaryKeyConstraint("campaign_id", name="pk_employee_referral_campaigns"),
        CheckConstraint(
            "campaign_id > 0 AND product_id > 0 AND content_revision > 0",
            name="ck_employee_referral_campaigns_positive",
        ),
        CheckConstraint(
            "state IN ('published', 'ended')", name="ck_employee_referral_campaigns_state"
        ),
        CheckConstraint("end_at > start_at", name="ck_employee_referral_campaigns_window"),
        CheckConstraint(
            "(state = 'published' AND ended_at IS NULL) OR (state = 'ended' AND ended_at IS NOT NULL AND published_at <= ended_at)",
            name="ck_employee_referral_campaigns_end",
        ),
        CheckConstraint(
            "valid_until = issued_at + interval '60 seconds'",
            name="ck_employee_referral_campaigns_lease",
        ),
    )
    campaign_id: Mapped[int] = mapped_column(BigInteger, autoincrement=False)
    content_revision: Mapped[int] = mapped_column(BigInteger)
    product_id: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(9))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_sha256: Mapped[str] = mapped_column(String(64))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReferralIdentity(Base):
    __tablename__ = "employee_referral_tokens"
    __table_args__ = (
        PrimaryKeyConstraint("token", name="pk_employee_referral_tokens"),
        UniqueConstraint("campaign_id", "employee_code", name="uq_employee_referral_tokens_pair"),
    )
    token: Mapped[str] = mapped_column(String(22))
    campaign_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "employee_referral_campaigns.campaign_id",
            name="fk_employee_referral_tokens_campaign",
            ondelete="RESTRICT",
        ),
    )
    employee_code: Mapped[UUID] = mapped_column(Uuid)
    employee_display_name: Mapped[str] = mapped_column(String(161))
    in_current_registry: Mapped[bool] = mapped_column(Boolean)


class K1Receipt(Base):
    __tablename__ = "employee_referral_k1_receipts"
    __table_args__ = (
        PrimaryKeyConstraint("event_id", name="pk_employee_referral_k1_receipts"),
        CheckConstraint("content_revision > 0", name="ck_employee_referral_k1_receipts_revision"),
        CheckConstraint(
            "outcome IN ('applied', 'renewed', 'unchanged', 'superseded')",
            name="ck_employee_referral_k1_receipts_outcome",
        ),
        Index("ix_employee_referral_k1_receipts_revision", "campaign_id", "content_revision"),
    )
    event_id: Mapped[UUID] = mapped_column(Uuid)
    campaign_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey(
            "employee_referral_campaigns.campaign_id",
            name="fk_employee_referral_k1_receipts_campaign",
            ondelete="RESTRICT",
        ),
    )
    content_revision: Mapped[int] = mapped_column(BigInteger)
    body_sha256: Mapped[str] = mapped_column(String(64))
    content_sha256: Mapped[str] = mapped_column(String(64))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(10))


class ReferralOrderSnapshot(Base):
    """Order-time identity; subsequent K1/catalogue changes are independent."""

    __tablename__ = "employee_referral_orders"
    __table_args__ = (
        PrimaryKeyConstraint("order_id", name="pk_employee_referral_orders"),
        CheckConstraint(
            "campaign_id > 0 AND product_id > 0 AND content_revision > 0",
            name="ck_employee_referral_orders_positive",
        ),
    )
    order_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("orders.id", name="fk_employee_referral_orders_order", ondelete="CASCADE"),
        autoincrement=False,
    )
    campaign_id: Mapped[int] = mapped_column(BigInteger)
    product_id: Mapped[int] = mapped_column(BigInteger)
    content_revision: Mapped[int] = mapped_column(BigInteger)
    campaign_name: Mapped[str] = mapped_column(String(200))
    employee_code: Mapped[UUID] = mapped_column(Uuid)
    employee_display_name: Mapped[str] = mapped_column(String(161))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_sha256: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3))
    items: Mapped[list["ReferralOrderItemSnapshot"]] = relationship(
        back_populates="header", cascade="all, delete-orphan"
    )


class ReferralOrderItemSnapshot(Base):
    __tablename__ = "employee_referral_order_items"
    __table_args__ = (
        PrimaryKeyConstraint("order_item_id", name="pk_employee_referral_order_items"),
        CheckConstraint(
            "product_id > 0 AND quantity > 0", name="ck_employee_referral_order_items_positive"
        ),
    )
    order_item_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "order_items.id", name="fk_employee_referral_order_items_item", ondelete="CASCADE"
        ),
        autoincrement=False,
    )
    order_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "employee_referral_orders.order_id",
            name="fk_employee_referral_order_items_header",
            ondelete="CASCADE",
        ),
    )
    product_id: Mapped[int] = mapped_column(BigInteger)
    product_name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    is_sale_at_order: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    regular_price_at_order: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    header: Mapped[ReferralOrderSnapshot] = relationship(back_populates="items")
