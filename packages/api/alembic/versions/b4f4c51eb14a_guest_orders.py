"""Allow website guest orders: optional owner, access/idempotency digests, legal acceptance.

Revision ID: b4f4c51eb14a
Revises: 8d1b1791159b
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "b4f4c51eb14a"
down_revision: str | Sequence[str] | None = "8d1b1791159b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GUEST_COLUMNS = (
    "guest_access_digest",
    "guest_idempotency_digest",
    "legal_terms_version",
    "legal_privacy_version",
    "legal_accepted_at",
)


def _prepare() -> tuple[str, str]:
    if context.is_offline_mode() or op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("Guest order migration requires online PostgreSQL")
    bind = op.get_bind()
    previous = (
        bind.execute(sa.text("SHOW lock_timeout")).scalar_one(),
        bind.execute(sa.text("SHOW statement_timeout")).scalar_one(),
    )
    bind.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    bind.execute(sa.text("SET LOCAL statement_timeout = '30s'"))
    return previous


def _restore(previous: tuple[str, str]) -> None:
    for name, value in zip(("lock_timeout", "statement_timeout"), previous, strict=True):
        op.get_bind().execute(
            sa.text("SELECT set_config(:name, :value, true)"), {"name": name, "value": value}
        )


def upgrade() -> None:
    previous = _prepare()
    op.alter_column("orders", "user_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("orders", sa.Column("guest_access_digest", sa.String(64)))
    op.add_column("orders", sa.Column("guest_idempotency_digest", sa.String(64)))
    op.add_column("orders", sa.Column("legal_terms_version", sa.String(64)))
    op.add_column("orders", sa.Column("legal_privacy_version", sa.String(64)))
    op.add_column("orders", sa.Column("legal_accepted_at", sa.DateTime(timezone=True)))
    op.create_index("uq_orders_guest_access_digest", "orders", ["guest_access_digest"], unique=True)
    op.create_index(
        "uq_orders_guest_idempotency_digest", "orders", ["guest_idempotency_digest"], unique=True
    )
    op.create_check_constraint(
        "orders_owner_ck",
        "orders",
        "user_id IS NOT NULL OR (guest_access_digest IS NOT NULL AND points_applied = 0 "
        "AND legal_terms_version IS NOT NULL AND legal_privacy_version IS NOT NULL "
        "AND legal_accepted_at IS NOT NULL)",
    )
    _restore(previous)


def downgrade() -> None:
    previous = _prepare()
    bind = op.get_bind()
    # Hold the table until the outer Alembic transaction ends, then refuse before any change.
    bind.execute(sa.text("LOCK TABLE orders IN ACCESS EXCLUSIVE MODE"))
    if bind.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM orders WHERE user_id IS NULL "
            "OR guest_access_digest IS NOT NULL OR guest_idempotency_digest IS NOT NULL "
            "OR legal_terms_version IS NOT NULL OR legal_privacy_version IS NOT NULL "
            "OR legal_accepted_at IS NOT NULL)"
        )
    ).scalar_one():
        raise RuntimeError("Guest order downgrade refused: retained guest order data")
    op.drop_constraint("orders_owner_ck", "orders", type_="check")
    op.drop_index("uq_orders_guest_idempotency_digest", table_name="orders")
    op.drop_index("uq_orders_guest_access_digest", table_name="orders")
    for name in reversed(GUEST_COLUMNS):
        op.drop_column("orders", name)
    op.alter_column("orders", "user_id", existing_type=sa.Integer(), nullable=False)
    _restore(previous)
