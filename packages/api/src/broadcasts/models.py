"""Broadcast SQLAlchemy models."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class BroadcastStatus(StrEnum):
    """Broadcast delivery status."""

    DRAFT = "draft"
    SENDING = "sending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Broadcast(Base):
    """Broadcast campaign model."""

    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Content
    title: Mapped[str] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    photo_url: Mapped[str | None] = mapped_column(Text)
    photo_file_id: Mapped[str | None] = mapped_column(String(255))
    button_text: Mapped[str | None] = mapped_column(String(64))
    button_url: Mapped[str | None] = mapped_column(Text)

    # Status
    status: Mapped[str] = mapped_column(
        String(20),
        default=BroadcastStatus.DRAFT,
    )

    # Counters
    total_users: Mapped[int] = mapped_column(Integer, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    blocked_count: Mapped[int] = mapped_column(Integer, default=0)

    # Meta
    created_by: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    def __repr__(self) -> str:
        return f"<Broadcast(id={self.id}, title={self.title!r}, status={self.status})>"
