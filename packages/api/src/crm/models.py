"""CRM SQLAlchemy models."""

import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database import Base


class LeadStatus(enum.StrEnum):
    """Lead status enum."""

    NEW = "new"
    IN_PROGRESS = "in_progress"
    CONVERTED = "converted"
    CLOSED = "closed"


class FamilyMember(Base):
    """Family member model for storing customer's family health data."""

    __tablename__ = "family_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    relation: Mapped[str | None] = mapped_column(String(100))
    age: Mapped[int | None] = mapped_column(Integer)
    symptoms: Mapped[list[str] | None] = mapped_column(ARRAY(Text), default=[])

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationship
    user = relationship("User", back_populates="family_members")

    def __repr__(self) -> str:
        return f"<FamilyMember(id={self.id}, name={self.name}, user_id={self.user_id})>"


class Lead(Base):
    """Lead model for AI assistant generated leads."""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    status: Mapped[LeadStatus] = mapped_column(
        Enum(LeadStatus, name="lead_status", create_type=True), default=LeadStatus.NEW, index=True
    )

    request_text: Mapped[str | None] = mapped_column(Text)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    recommended_products: Mapped[list[str] | None] = mapped_column(ARRAY(Text), default=[])
    manager_notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationship
    user = relationship("User", back_populates="leads")

    def __repr__(self) -> str:
        return f"<Lead(id={self.id}, status={self.status}, telegram_id={self.telegram_id})>"
