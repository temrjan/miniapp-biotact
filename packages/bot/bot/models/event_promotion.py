"""Event promotion tables (spec event-qr-discount R-03; shared schema with the API).

The API owns these tables and their migration (``87688cb444f4``). The bot reads promotions and
writes grants and scans; it never maps or writes order tables.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from bot.database import Base


class EventPromotion(Base):
    """One event offer. Read-only for the bot; the API operator command writes it."""

    __tablename__ = "event_promotions"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    title: Mapped[str] = mapped_column(String(60))
    percent: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EventPromotionGrant(Base):
    """A user's right to a promotion's discount; unique per promotion and user."""

    __tablename__ = "event_promotion_grants"
    __table_args__ = (
        UniqueConstraint("promotion_id", "user_id", name="uq_event_promotion_grants_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    promotion_id: Mapped[int] = mapped_column(ForeignKey("event_promotions.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_new_user: Mapped[bool] = mapped_column(Boolean)
    first_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventPromotionScan(Base):
    """One received ``/start ev_<code>`` for a known promotion, with its outcome."""

    __tablename__ = "event_promotion_scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    promotion_id: Mapped[int] = mapped_column(ForeignKey("event_promotions.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str] = mapped_column(String(20))
