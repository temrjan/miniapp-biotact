"""Payment SQLAlchemy models."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class PaymentEvent(Base):
    """Append-only receipts of Multicard payment events; receipt existence is not settlement.

    One payment sends us SEVERAL events over its life (``progress`` → ``success``, and possibly
    ``revert`` later). Storing only the order's latest ``payment_status`` cannot answer "did we
    already handle this exact event?" — the question every event handler must answer before it
    moves money or (from Stage 6) points. This table answers it atomically.

    Idempotency key: ``UNIQUE(payment_uuid, status)``. The handler does
    ``INSERT ... ON CONFLICT DO NOTHING``; if 0 rows land, this (uuid, status) was already
    received. Ordinary handling may recover a recognized untrusted success hint only for an
    eligible first trusted settlement; applied callbacks remain no-ops.

    Append-only: written once per event, never updated or deleted in code. ``payload`` snapshots
    the validated callback/webhook model so a reconciliation can always be reconstructed from source.
    """

    __tablename__ = "payment_event"
    __table_args__ = (
        # Receipt uniqueness: at most one retained row per (payment, status).
        # Settlement eligibility is arbitrated separately under the order lock.
        Index(
            "uq_payment_event_uuid_status",
            "payment_uuid",
            "status",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    payment_uuid: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(20))
    # Amount as reported by Multicard, in tiyins (1 сум = 100 тийин). Snapshotted for the
    # amount-reconciliation check and the audit trail; never recomputed from it.
    amount: Mapped[int] = mapped_column(BigInteger)
    invoice_id: Mapped[str] = mapped_column(String(255), index=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<PaymentEvent(id={self.id}, payment_uuid={self.payment_uuid}, status={self.status})>"
        )
