"""Event promotion storage (spec event-qr-discount S3, R-02).

The API owns these tables and their migration. The Shop bot writes grants and scans and reads
promotions through its own mapping (R-03); the API reads grants for pricing and the "my discount"
read. A grant has no dates of its own: it is valid exactly while its promotion's window is open.
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base

SCAN_OUTCOMES = ("granted", "existing_grant", "outside_window")


class EventPromotion(Base):
    """One event offer: its code (the ``ev_<code>`` deep link), title, percent and UTC window."""

    __tablename__ = "event_promotions"
    __table_args__ = (
        CheckConstraint("percent >= 1 AND percent <= 90", name="event_promotions_percent_ck"),
        CheckConstraint("starts_at < ends_at", name="event_promotions_window_ck"),
        CheckConstraint("char_length(title) >= 1", name="event_promotions_title_ck"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    title: Mapped[str] = mapped_column(String(60))
    percent: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventPromotionGrant(Base):
    """A user's personal right to a promotion's discount, received by scanning its QR."""

    __tablename__ = "event_promotion_grants"
    __table_args__ = (
        UniqueConstraint("promotion_id", "user_id", name="uq_event_promotion_grants_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    promotion_id: Mapped[int] = mapped_column(
        ForeignKey("event_promotions.id", ondelete="RESTRICT")
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_new_user: Mapped[bool] = mapped_column(Boolean)
    # Set once, by the first "my discount" read that answers active (R-21).
    first_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventPromotionScan(Base):
    """One received ``/start ev_<code>`` for a known promotion, with what it produced."""

    __tablename__ = "event_promotion_scans"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('granted', 'existing_grant', 'outside_window')",
            name="event_promotion_scans_outcome_ck",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    promotion_id: Mapped[int] = mapped_column(
        ForeignKey("event_promotions.id", ondelete="RESTRICT"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    outcome: Mapped[str] = mapped_column(String(20))
