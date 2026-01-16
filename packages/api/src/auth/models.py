"""Admin user model for JWT authentication."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Boolean, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class AdminUser(Base):
    """Admin user model with email/password authentication."""

    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    
    # Profile
    first_name: Mapped[Optional[str]] = mapped_column(String(100))
    last_name: Mapped[Optional[str]] = mapped_column(String(100))
    
    # Email verification
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_token: Mapped[Optional[str]] = mapped_column(String(100))
    verification_token_expires: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    # Password reset
    reset_token: Mapped[Optional[str]] = mapped_column(String(100))
    reset_token_expires: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    
    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_login: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    def __repr__(self) -> str:
        return f"<AdminUser(id={self.id}, email={self.email})>"
