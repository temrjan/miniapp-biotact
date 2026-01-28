"""User SQLAlchemy models."""

from datetime import datetime
from typing import Optional, List, TYPE_CHECKING

from sqlalchemy import String, Boolean, DateTime, BigInteger, Text, Float, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

if TYPE_CHECKING:
    from src.crm.models import FamilyMember, Lead

from src.database import Base


class User(Base):
    """User model."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(100))
    first_name: Mapped[Optional[str]] = mapped_column(String(100))
    last_name: Mapped[Optional[str]] = mapped_column(String(100))
    language_code: Mapped[str] = mapped_column(String(10), default="ru")
    
    # Contact
    phone: Mapped[Optional[str]] = mapped_column(String(20))
    
    # Default delivery address
    address_street: Mapped[Optional[str]] = mapped_column(String(255))
    address_house: Mapped[Optional[str]] = mapped_column(String(20))
    address_apartment: Mapped[Optional[str]] = mapped_column(String(20))
    address_entrance: Mapped[Optional[str]] = mapped_column(String(10))
    address_floor: Mapped[Optional[str]] = mapped_column(String(10))
    address_comment: Mapped[Optional[str]] = mapped_column(Text)
    address_lat: Mapped[Optional[float]] = mapped_column(Float)
    address_lng: Mapped[Optional[float]] = mapped_column(Float)

    # CRM data (AI assistant collected)
    symptoms: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text), default=[])
    ai_notes: Mapped[Optional[str]] = mapped_column(Text)

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)

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
    family_members: Mapped[List["FamilyMember"]] = relationship(
        "FamilyMember",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    leads: Mapped[List["Lead"]] = relationship(
        "Lead",
        back_populates="user"
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, telegram_id={self.telegram_id})>"
