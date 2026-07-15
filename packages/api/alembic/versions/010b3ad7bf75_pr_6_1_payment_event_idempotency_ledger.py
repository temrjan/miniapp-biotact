"""PR-6.1 payment_event idempotency ledger

Revision ID: 010b3ad7bf75
Revises: 2530036167a8
Create Date: 2026-07-15 08:43:32.361058

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "010b3ad7bf75"
down_revision: str | Sequence[str] | None = "2530036167a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the payment-event idempotency ledger (PR-6.1 precondition for Stage 6 REDEEM).

    One Multicard payment emits several events over its life (``progress`` → ``success``, and
    possibly ``revert``). The order row alone cannot answer "did we already handle THIS event?",
    which every handler must answer before it moves money or points. This table answers it:

    * ``UNIQUE(payment_uuid, status)`` is the idempotency key. The handler does
      ``INSERT ... ON CONFLICT DO NOTHING``; 0 rows ⇒ this (uuid, status) was already processed
      ⇒ no re-apply, no duplicate admin notification. Same proven pattern as the loyalty ledger's
      partial ``UNIQUE(qr_code_id, reason)``.
    * ``payload`` (JSONB) snapshots the raw callback/webhook body for reconciliation.

    Additive only: no existing table is touched, so a code rollback leaves the schema harmless.
    """
    op.create_table(
        "payment_event",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("payment_uuid", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("invoice_id", sa.String(length=255), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_payment_event_invoice_id"), "payment_event", ["invoice_id"], unique=False
    )
    op.create_index(
        op.f("ix_payment_event_payment_uuid"), "payment_event", ["payment_uuid"], unique=False
    )
    op.create_index(
        "uq_payment_event_uuid_status",
        "payment_event",
        ["payment_uuid", "status"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_payment_event_uuid_status", table_name="payment_event")
    op.drop_index(op.f("ix_payment_event_payment_uuid"), table_name="payment_event")
    op.drop_index(op.f("ix_payment_event_invoice_id"), table_name="payment_event")
    op.drop_table("payment_event")
