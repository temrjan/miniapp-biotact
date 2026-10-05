"""Two retained effect records; immutable outboxes alone own K2 bytes."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


def constraints(lane: str, terminal: str) -> tuple[CheckConstraint | Index, ...]:
    return (
        CheckConstraint(
            "(source_kind = 'paid' AND paid_id IS NOT NULL AND return_id IS NULL) OR "
            "(source_kind = 'return' AND paid_id IS NULL AND return_id IS NOT NULL)",
            name=f"ck_referral_{lane}_source",
        ),
        CheckConstraint(
            "attempt_count BETWEEN 0 AND 5 AND generation >= 0",
            name=f"ck_referral_{lane}_attempt",
        ),
        CheckConstraint(
            f"state IN ('pending','leased','blocked','{terminal}'"
            + (",'legacy_unknown')" if lane == "notice" else ")"),
            name=f"ck_referral_{lane}_state",
        ),
        CheckConstraint(
            "(state = 'leased') = (leased_until IS NOT NULL)",
            name=f"ck_referral_{lane}_lease",
        ),
        CheckConstraint(
            f"(state = '{terminal}') = (completed_at IS NOT NULL) AND "
            + (
                "(state = 'sent') = (message_id IS NOT NULL) AND (message_id IS NULL OR message_id > 0)"
                if lane == "notice"
                else "(state = 'acknowledged') = (ack IS NOT NULL)"
            ),
            name=f"ck_referral_{lane}_receipt",
        ),
        Index(f"ix_referral_{lane}_due", "state", "next_attempt_at"),
    )


class DeliveryColumns(Base):
    __abstract__ = True
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_kind: Mapped[str] = mapped_column(String(6))
    paid_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("employee_referral_paid_outbox.id", ondelete="RESTRICT"), unique=True
    )
    return_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("employee_referral_return_outbox.id", ondelete="RESTRICT"),
        unique=True,
    )
    state: Mapped[str] = mapped_column(String(16))
    attempt_count: Mapped[int] = mapped_column(BigInteger)
    generation: Mapped[int] = mapped_column(BigInteger)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_category: Mapped[str | None] = mapped_column(String(32))


class K2Delivery(DeliveryColumns):
    __tablename__ = "employee_referral_k2_delivery"
    __table_args__ = constraints("k2", "acknowledged")
    ack: Mapped[dict[str, object] | None] = mapped_column(JSONB)


class NoticeDelivery(DeliveryColumns):
    __tablename__ = "employee_referral_notice_delivery"
    __table_args__ = (
        *constraints("notice", "sent"),
        CheckConstraint(
            "(state = 'legacy_unknown' AND payload IS NULL AND payload_sha256 IS NULL) OR "
            "(state <> 'legacy_unknown' AND payload IS NOT NULL AND payload_sha256 IS NOT NULL "
            "AND octet_length(payload) BETWEEN 1 AND 1048576 AND payload_sha256 ~ '^[0-9a-f]{64}$')",
            name="ck_referral_notice_payload",
        ),
    )
    payload: Mapped[bytes | None] = mapped_column(LargeBinary)
    payload_sha256: Mapped[str | None] = mapped_column(String(64))
    message_id: Mapped[int | None] = mapped_column(BigInteger)
