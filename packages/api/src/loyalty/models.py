"""Biotact Club loyalty SQLAlchemy models."""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


class ConsentPurpose(enum.StrEnum):
    """Purpose a user consent applies to (per RU/UZ data-protection law)."""

    DATA_PROCESSING = "data_processing"
    MARKETING = "marketing"
    PERSONALIZATION = "personalization"


class Consent(Base):
    """Per-purpose, versioned, timestamped user consent.

    Append-style: a new row per (re)grant/revoke; current state is the latest row
    per (user, purpose). No endpoints at Stage 1 — model + table only.
    """

    __tablename__ = "consent"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[ConsentPurpose] = mapped_column(
        Enum(ConsentPurpose, name="consent_purpose", create_type=True)
    )
    granted: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[str] = mapped_column(String(50))
    source: Mapped[str | None] = mapped_column(String(50))

    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", back_populates="consents")

    def __repr__(self) -> str:
        return f"<Consent(id={self.id}, user_id={self.user_id}, purpose={self.purpose})>"


class Gender(enum.StrEnum):
    """Customer-declared gender (optional survey field)."""

    MALE = "male"
    FEMALE = "female"


class LoyaltyStatus(enum.StrEnum):
    """Loyalty tier. ``newbie`` → ``biotact_friend`` after 10 lifetime activations."""

    NEWBIE = "newbie"
    BIOTACT_FRIEND = "biotact_friend"


class CustomerProfile(Base):
    """1:1 loyalty/CRM profile attached to a ``users`` row.

    Holds the progressive survey + loyalty status/counters. Created lazily once a
    customer enters the loyalty flow (Stage ≥4); absence breaks nothing. Balance is
    NOT stored here — it is ``SUM(point_ledger)`` (Stage 2). No endpoints at Stage 1.
    """

    __tablename__ = "customer_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)

    # Progressive survey (filled over time; absence = not asked yet).
    gender: Mapped[Gender | None] = mapped_column(Enum(Gender, name="gender", create_type=True))
    age: Mapped[int | None] = mapped_column(Integer)
    city: Mapped[str | None] = mapped_column(String(100))
    purchase_goal: Mapped[str | None] = mapped_column(String(50))
    purchase_for_whom: Mapped[str | None] = mapped_column(String(50))

    # Loyalty status + counters.
    status: Mapped[LoyaltyStatus] = mapped_column(
        Enum(LoyaltyStatus, name="loyalty_status", create_type=True),
        default=LoyaltyStatus.NEWBIE,
        server_default=LoyaltyStatus.NEWBIE.name,  # PG stores StrEnum as NAME
    )
    lifetime_activations: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_activation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_accrual_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_activated_sku: Mapped[str | None] = mapped_column(String(32))
    registration_source: Mapped[str | None] = mapped_column(String(20))

    # Growing survey blob — extend without migrations.
    survey_raw: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", back_populates="profile")

    def __repr__(self) -> str:
        return f"<CustomerProfile(id={self.id}, user_id={self.user_id}, status={self.status})>"


class CustomerEventType(enum.StrEnum):
    """Canonical customer-event vocabulary.

    Stored as a free string (column is ``String``, NOT a PG enum) so the set can
    grow without a migration per new type. These constants exist to avoid magic
    strings in the loyalty services (Stage ≥2).
    """

    JOINED = "joined"
    PHONE_SHARED = "phone_shared"
    QR_ACTIVATED = "qr_activated"
    SURVEY_DONE = "survey_done"
    STATUS_FRIEND = "status_friend"
    RAFFLE_ENTRY = "raffle_entry"
    ORDER_PLACED = "order_placed"
    POINTS_SPENT = "points_spent"
    POINTS_EXPIRED = "points_expired"


class CustomerEvent(Base):
    """Append-only customer timeline.

    High-volume: queried by ``user_id`` directly (no ORM relationship on ``User``),
    never updated or deleted in code — rows disappear only via the DB
    ``ON DELETE CASCADE`` when a user is removed. ``event_type`` is a free string
    (constants in :class:`CustomerEventType`), not a PG enum. No endpoints at Stage 1.
    """

    __tablename__ = "customer_event"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<CustomerEvent(id={self.id}, user_id={self.user_id}, event_type={self.event_type})>"
        )


class LedgerReason(enum.StrEnum):
    """Canonical point-ledger reason vocabulary (stored as a free String, NOT a PG enum).

    Stage 2 writes only ``CASHBACK`` and ``WELCOME``. The rest are the canonical set
    for later stages (``FRIEND_BONUS`` = Stage 3; ``REDEEM``/``EXPIRY`` = Stage 6;
    ``REFERRAL``/``BIRTHDAY``/``REVIEW``/``SUBSCRIPTION``/``L3`` = Stage 7). A String
    column lets the vocabulary grow without a migration per reason — same approach as
    :class:`CustomerEventType`.
    """

    CASHBACK = "cashback"
    WELCOME = "welcome"
    FRIEND_BONUS = "friend_bonus"
    REFERRAL = "referral"
    BIRTHDAY = "birthday"
    REVIEW = "review"
    SUBSCRIPTION = "subscription"
    L3 = "l3"
    REDEEM = "redeem"
    EXPIRY = "expiry"


class PointLedger(Base):
    """Append-only points ledger. Balance is ``SUM(amount)`` — never a stored column.

    Money truth: written once, never updated or deleted. ``amount`` is a signed integer
    of UZS-points (1 балл = 1 сум) — positive on earn (cashback/welcome/…), negative on
    redeem/expiry. Earn rows snapshot their computation inputs (``sku``/``unit_price``/
    ``rate_bps``) so each row is self-contained and audit-reconcilable independently of
    later price or status changes; non-earn rows leave them NULL. ``reason`` is a free
    String (constants in :class:`LedgerReason`), not a PG enum.

    Idempotency: partial ``UNIQUE(qr_code_id, reason)`` (earn rows only) — at most one
    cashback and one welcome per activated code, even under a concurrent claim race.
    ``qr_code_id`` is a plain column here; its FK to ``qr_codes`` is added in PR-2.2,
    where that table is created. No endpoints at PR-2.1.
    """

    __tablename__ = "point_ledger"
    __table_args__ = (
        Index(
            "uq_point_ledger_qr_code_id_reason",
            "qr_code_id",
            "reason",
            unique=True,
            postgresql_where=text("qr_code_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    amount: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(32), index=True)

    # Idempotency key for earn rows tied to a QR activation. RESTRICT keeps money truth
    # intact: a qr_code with ledger rows cannot be deleted (user deletion removes the
    # ledger rows first via user_id CASCADE, so it never blocks deleting a user).
    qr_code_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("qr_codes.id", ondelete="RESTRICT", name="fk_point_ledger_qr_codes"),
        index=True,
    )

    # Provenance snapshot — earn rows only; NULL for redeem/expiry/adjust.
    sku: Mapped[str | None] = mapped_column(String(32))
    unit_price: Mapped[int | None] = mapped_column(BigInteger)
    rate_bps: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<PointLedger(id={self.id}, user_id={self.user_id}, "
            f"reason={self.reason}, amount={self.amount})>"
        )


class QrCodeStatus(enum.StrEnum):
    """QR lifecycle, stored as a free String (not a PG enum). No ``armed`` state —
    codes are active on print (anti-fraud arming deferred; leaked-code risk accepted).
    """

    GENERATED = "generated"
    USED = "used"


class CodeBatch(Base):
    """A generated batch of unique QR codes for one SKU (printed onto boxes)."""

    __tablename__ = "code_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(32), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<CodeBatch(id={self.id}, sku={self.sku}, quantity={self.quantity})>"


class QrCode(Base):
    """A unique QR code carrying a SKU. The code string is an opaque random token; the
    authoritative SKU lives in the ``sku`` column (never parsed from the string, which
    could be spoofed). Status flips ``generated`` → ``used`` atomically on activation
    (PR-2.3); ``UNIQUE(code)`` is the lookup key for the bot deeplink ``qr_<code>``.
    """

    __tablename__ = "qr_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    sku: Mapped[str] = mapped_column(String(32), index=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("code_batches.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(16),
        default=QrCodeStatus.GENERATED,
        server_default=QrCodeStatus.GENERATED.value,
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # SET NULL on user delete: the physical code stays 'used' in history, activator forgotten.
    activated_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<QrCode(id={self.id}, code={self.code}, sku={self.sku}, status={self.status})>"
