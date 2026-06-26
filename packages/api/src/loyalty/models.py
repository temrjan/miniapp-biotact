"""Biotact Club loyalty SQLAlchemy models."""

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, func
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
