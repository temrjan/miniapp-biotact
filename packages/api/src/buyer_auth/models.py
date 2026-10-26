"""Durable buyer credentials, proof state, sessions and rolling abuse windows."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class BuyerEmailIdentity(Base):
    __tablename__ = "buyer_email_identities"

    email: Mapped[str] = mapped_column(String(254), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BuyerEmailChallenge(Base):
    __tablename__ = "buyer_email_challenges"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    browser_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    code_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BuyerSession(Base):
    __tablename__ = "buyer_sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    verifier_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    authenticated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idle_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BuyerAuthLimit(Base):
    __tablename__ = "buyer_auth_limits"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    events: Mapped[list[float]] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, nullable=False
    )


class BuyerTelegramRequest(Base):
    """One context-bound login proof; a completed row also seals its context."""

    __tablename__ = "buyer_telegram_requests"
    __table_args__ = (
        UniqueConstraint("browser_digest", "request_key", name="uq_buyer_telegram_browser_key"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    browser_digest: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    request_key: Mapped[str] = mapped_column(String(32), nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expected_actor: Mapped[int | None] = mapped_column(BigInteger)
    deciding_actor: Mapped[int | None] = mapped_column(BigInteger)
    decision: Mapped[str | None] = mapped_column(String(8))
    delivery: Mapped[str] = mapped_column(String(16), nullable=False)
    completed_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    completed_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("buyer_sessions.id", ondelete="SET NULL")
    )
    completed_generation: Mapped[int | None] = mapped_column(Integer)
