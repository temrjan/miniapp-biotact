"""model-1: composite index orders(user_id, payment_status)

Revision ID: b7d2f4a9c1e3
Revises: 010b3ad7bf75
Create Date: 2026-07-16 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d2f4a9c1e3"
down_revision: str | Sequence[str] | None = "010b3ad7bf75"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Model-1: index the partial-redemption reserve subquery.

    A partial redemption reserves points by keeping an unpaid order alive with NO ledger row;
    admitting a new redemption sums ``points_applied`` over the user's still-open orders under a
    per-user lock. This composite index serves that hot filter —
    ``WHERE user_id = ? AND payment_status IN ('unpaid','pending')`` — keeping it index-only as
    order volume grows. Additive only: no column is touched, so a code rollback leaves the schema
    harmless (a plain non-unique B-tree). A regular CREATE INDEX (not CONCURRENTLY) is deliberate —
    the migration must run inside Alembic's transaction for the Migration-parity round-trip, and the
    ``orders`` table is small enough that the brief lock is a non-issue.
    """
    op.create_index(
        "ix_orders_user_id_payment_status",
        "orders",
        ["user_id", "payment_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_orders_user_id_payment_status", table_name="orders")
