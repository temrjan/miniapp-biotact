"""Biotact Club loyalty SQLAlchemy models."""

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, func
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
