"""Banner SQLAlchemy models."""

from datetime import datetime
from typing import Optional

from sqlalchemy import String, Boolean, DateTime, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class Banner(Base):
    """Banner model for homepage slider."""

    __tablename__ = "banners"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    title_uz: Mapped[Optional[str]] = mapped_column(String(200))
    subtitle: Mapped[Optional[str]] = mapped_column(String(500))
    subtitle_uz: Mapped[Optional[str]] = mapped_column(String(500))
    image_url: Mapped[Optional[str]] = mapped_column(String(500))
    gradient: Mapped[Optional[str]] = mapped_column(String(200))
    link_url: Mapped[Optional[str]] = mapped_column(String(500))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<Banner(id={self.id}, title={self.title})>"


class Settings(Base):
    """App settings model."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500))

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<Settings(key={self.key}, value={self.value})>"
