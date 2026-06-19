"""Loyalty SQLAlchemy models — Biotact Club phase 1.

Design notes:
- ``users.balance`` is a materialized cache; the source of truth is the
  append-only ``point_transactions`` ledger.
- A code is redeemable only when its batch is armed (``CodeBatch.is_armed``,
  flipped after shipment to retail) AND the code itself is unused
  (``QrCode.is_used``).
- Foreign keys to ``users`` use string targets so this module never imports
  ``src.users.models`` (no circular import). ``LoyaltyStatus`` lives here and
  is imported by the User model.
"""

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


class LoyaltyStatus(enum.StrEnum):
    """Loyalty tier. Phase 1 only ever assigns NEWBIE; BIOTACT_FRIEND is wired
    in phase 2 (10 activations → 8% cashback)."""

    NEWBIE = "newbie"
    BIOTACT_FRIEND = "biotact_friend"


class DrawStatus(enum.StrEnum):
    """Lottery draw lifecycle: open for tickets → committed (snapshot frozen,
    hash published) → drawn (winner revealed)."""

    OPEN = "open"
    COMMITTED = "committed"
    DRAWN = "drawn"


class TransactionReason(enum.StrEnum):
    """Ledger entry reason. Stored as a plain string column (not a PG enum)."""

    ACTIVATION = "activation"
    WELCOME_BONUS = "welcome_bonus"


class TicketSource(enum.StrEnum):
    """Where a lottery ticket came from. Stored as a plain string column."""

    SURVEY_L1 = "survey_l1"
    SURVEY_L2 = "survey_l2"


class CodeBatch(Base):
    """A printed batch of unique QR codes for one SKU.

    Carries the cashback basis (``unit_price``) so accrual never depends on the
    state of the ``products`` catalog. Codes "sleep" until ``is_armed`` is set
    after the batch ships to retail.
    """

    __tablename__ = "code_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku_code: Mapped[str] = mapped_column(String(32), index=True)
    sku_name: Mapped[str] = mapped_column(String(200))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    quantity: Mapped[int] = mapped_column(Integer)
    is_armed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    armed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    codes: Mapped[list["QrCode"]] = relationship(
        "QrCode", back_populates="batch", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<CodeBatch(id={self.id}, sku_code={self.sku_code}, qty={self.quantity})>"


class QrCode(Base):
    """A single unique QR code on a box flap."""

    __tablename__ = "qr_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("code_batches.id", ondelete="CASCADE"), index=True
    )
    is_used: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    batch: Mapped["CodeBatch"] = relationship("CodeBatch", back_populates="codes")

    def __repr__(self) -> str:
        return f"<QrCode(id={self.id}, code={self.code}, used={self.is_used})>"


class PointTransaction(Base):
    """Append-only ledger of point movements. ``users.balance`` == SUM(amount)."""

    __tablename__ = "point_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    amount: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(32), index=True)
    qr_code_id: Mapped[int | None] = mapped_column(ForeignKey("qr_codes.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    def __repr__(self) -> str:
        return (
            f"<PointTransaction(id={self.id}, user_id={self.user_id}, "
            f"amount={self.amount}, reason={self.reason})>"
        )


class SurveyAnswer(Base):
    """One stored survey answer. Re-answering a key updates the same row."""

    __tablename__ = "survey_answers"
    __table_args__ = (UniqueConstraint("user_id", "question_key", name="uq_survey_user_question"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    level: Mapped[int] = mapped_column(Integer)
    question_key: Mapped[str] = mapped_column(String(50))
    answer: Mapped[str] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<SurveyAnswer(user_id={self.user_id}, key={self.question_key})>"


class LotteryDraw(Base):
    """A monthly draw. Exactly one draw is OPEN at a time.

    Provable via commit-reveal: at commit time the participant snapshot is
    frozen and ``commit_hash`` = SHA-256(server_seed ‖ snapshot) is published;
    after the draw ``server_seed`` is revealed so anyone can reproduce it.
    """

    __tablename__ = "lottery_draws"

    id: Mapped[int] = mapped_column(primary_key=True)
    period: Mapped[str] = mapped_column(String(7), unique=True, index=True)  # YYYY-MM
    status: Mapped[DrawStatus] = mapped_column(
        Enum(DrawStatus, name="draw_status", create_type=True),
        default=DrawStatus.OPEN,
        index=True,
    )
    draw_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    commit_hash: Mapped[str | None] = mapped_column(String(64))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    server_seed: Mapped[str | None] = mapped_column(String(128))
    total_tickets: Mapped[int | None] = mapped_column(Integer)
    winner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    drawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<LotteryDraw(id={self.id}, period={self.period}, status={self.status})>"


class LotteryTicket(Base):
    """One ticket in a draw. A user gets at most one ticket per source per draw
    (phase 1: L1 → 1 ticket, L2 → +1 ticket; cap of 2)."""

    __tablename__ = "lottery_tickets"
    __table_args__ = (
        UniqueConstraint("draw_id", "user_id", "source", name="uq_ticket_user_source"),
        UniqueConstraint("draw_id", "ticket_number", name="uq_ticket_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    draw_id: Mapped[int] = mapped_column(
        ForeignKey("lottery_draws.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ticket_number: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<LotteryTicket(draw_id={self.draw_id}, user_id={self.user_id}, "
            f"number={self.ticket_number})>"
        )
