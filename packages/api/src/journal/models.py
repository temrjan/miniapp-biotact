"""Journal tables (spec.opyt r2 JT-01, JT-02): one row per conversation key and per stored
consultant turn."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base

# Python None is stored as SQL NULL, never as the JSON value null.
JSON_COLUMN = JSONB(none_as_null=True)


class Conversation(Base):
    __tablename__ = "consultant_conversation"
    __table_args__ = (UniqueConstraint("tab_id", "token_epoch"),)
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    channel: Mapped[str] = mapped_column(Text)
    tab_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    token_epoch: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_turn_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JournalTurn(Base):
    __tablename__ = "consultant_turn"
    __table_args__ = (
        CheckConstraint("agreed_source IN ('jev_luna','confirmed')"),
        Index("ix_consultant_turn_conversation_id_id", "conversation_id", "id"),
    )
    # The order of writing.
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    conversation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("consultant_conversation.id", ondelete="CASCADE")
    )
    tab_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    written_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    fault_code: Mapped[str | None] = mapped_column(Text)
    response_status: Mapped[str | None] = mapped_column(Text)
    path: Mapped[str] = mapped_column(Text)
    graf_outcome: Mapped[str | None] = mapped_column(Text)
    situation: Mapped[str | None] = mapped_column(Text)
    template: Mapped[str | None] = mapped_column(Text)
    offered_product_ids: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), server_default=text("'{}'")
    )
    jev: Mapped[dict[str, Any] | None] = mapped_column(JSON_COLUMN)
    luna: Mapped[dict[str, Any] | None] = mapped_column(JSON_COLUMN)
    facts: Mapped[dict[str, Any] | None] = mapped_column(JSON_COLUMN)
    versions: Mapped[dict[str, Any] | None] = mapped_column(JSON_COLUMN)
    asked_question: Mapped[dict[str, Any] | None] = mapped_column(JSON_COLUMN)
    resolution: Mapped[dict[str, Any] | None] = mapped_column(JSON_COLUMN)
    tokens: Mapped[list[str]] = mapped_column(ARRAY(Text))
    agreed_situation: Mapped[str | None] = mapped_column(Text)
    agreed_source: Mapped[str | None] = mapped_column(Text)
    # Written by PR-6 (Opyt pass) and PR-7 (memory).
    opyt: Mapped[dict[str, Any] | None] = mapped_column(JSON_COLUMN)
    memory: Mapped[dict[str, Any] | None] = mapped_column(JSON_COLUMN)
