"""One immutable admin confirmation and frozen outbox, sharing the paid sequence."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class ReturnOutbox(Base):
    __tablename__ = "employee_referral_return_outbox"
    __table_args__ = (
        UniqueConstraint("order_id", "order_version", name="uq_referral_return_version"),
        CheckConstraint(
            "id > 0 AND order_id > 0 AND order_version >= 2 AND confirmed_by > 0",
            name="ck_referral_return_positive",
        ),
        CheckConstraint("source = 'shop-admin-confirmation'", name="ck_referral_return_source"),
        CheckConstraint(
            "octet_length(body) BETWEEN 1 AND 1048576 AND body_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_referral_return_body",
        ),
        CheckConstraint(
            "substring(return_id::text,15,1) = '4' AND substring(return_id::text,20,1) IN ('8','9','a','b')",
            name="ck_referral_return_uuid",
        ),
    )
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=False,
        server_default=text("nextval('employee_referral_paid_outbox_id_seq'::regclass)"),
    )
    return_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    order_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employee_referral_paid_outbox.order_id", ondelete="RESTRICT")
    )
    order_version: Mapped[int] = mapped_column(BigInteger)
    confirmed_by: Mapped[int] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(32))
    request_lines: Mapped[list[dict[str, str]]] = mapped_column(JSONB)
    body: Mapped[bytes] = mapped_column(LargeBinary)
    body_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
