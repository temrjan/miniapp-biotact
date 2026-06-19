"""User SQLAlchemy models."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from src.crm.models import FamilyMember, Lead

from src.database import Base
from src.loyalty.models import LoyaltyStatus


class User(Base):
    """User model."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(100))
    first_name: Mapped[str | None] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    language_code: Mapped[str] = mapped_column(String(10), default="ru")

    # Contact
    phone: Mapped[str | None] = mapped_column(String(20))

    # Default delivery address
    address_street: Mapped[str | None] = mapped_column(String(255))
    address_house: Mapped[str | None] = mapped_column(String(20))
    address_apartment: Mapped[str | None] = mapped_column(String(20))
    address_entrance: Mapped[str | None] = mapped_column(String(10))
    address_floor: Mapped[str | None] = mapped_column(String(10))
    address_comment: Mapped[str | None] = mapped_column(Text)
    address_lat: Mapped[float | None] = mapped_column(Float)
    address_lng: Mapped[float | None] = mapped_column(Float)

    # CRM data (AI assistant collected)
    symptoms: Mapped[list[str] | None] = mapped_column(ARRAY(Text), default=[])
    ai_notes: Mapped[str | None] = mapped_column(Text)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)

    # Loyalty (Biotact Club). `balance` is a cache of the point_transactions ledger.
    balance: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    loyalty_status: Mapped[LoyaltyStatus] = mapped_column(
        Enum(LoyaltyStatus, name="loyalty_status", create_type=True),
        default=LoyaltyStatus.NEWBIE,
        # PG enum labels are member NAMES (project convention, cf. lead_status),
        # so the server_default must be the name, not the lowercase value.
        server_default=LoyaltyStatus.NEWBIE.name,
    )
    lifetime_activations: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_activation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Survey-derived profile (denormalized for CRM / segmentation)
    gender: Mapped[str | None] = mapped_column(String(10))
    age: Mapped[int | None] = mapped_column(Integer)
    city: Mapped[str | None] = mapped_column(String(100))
    purchase_goal: Mapped[str | None] = mapped_column(String(50))
    purchase_for_whom: Mapped[str | None] = mapped_column(String(50))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    family_members: Mapped[list["FamilyMember"]] = relationship(
        "FamilyMember", back_populates="user", cascade="all, delete-orphan"
    )
    leads: Mapped[list["Lead"]] = relationship("Lead", back_populates="user")

    def __repr__(self) -> str:
        return f"<User(id={self.id}, telegram_id={self.telegram_id})>"
