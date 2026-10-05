"""Frozen paid count history survives deletion of the original order."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, LargeBinary, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class PaidOutbox(Base):
    __tablename__ = "employee_referral_paid_outbox"
    __table_args__ = (
        CheckConstraint("id > 0 AND order_id > 0", name="ck_referral_paid_positive"),
        CheckConstraint(
            "octet_length(body) BETWEEN 1 AND 1048576 AND body_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_referral_paid_body",
        ),
        CheckConstraint(
            "substring(event_id::text,15,1) = '4' AND substring(event_id::text,20,1) IN ('8','9','a','b')",
            name="ck_referral_paid_uuid",
        ),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    event_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    body: Mapped[bytes] = mapped_column(LargeBinary)
    body_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
