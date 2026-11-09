"""Event promotions: promotion, grant and scan tables; order reference and line discount.

Revision ID: 87688cb444f4
Revises: b4f4c51eb14a
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "87688cb444f4"
down_revision: str | Sequence[str] | None = "b4f4c51eb14a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _prepare() -> tuple[str, str]:
    if context.is_offline_mode() or op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("Event promotion migration requires online PostgreSQL")
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
    op.create_table(
        "event_promotions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("title", sa.String(60), nullable=False),
        sa.Column("percent", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("code"),
        sa.CheckConstraint("percent >= 1 AND percent <= 90", name="event_promotions_percent_ck"),
        sa.CheckConstraint("starts_at < ends_at", name="event_promotions_window_ck"),
        sa.CheckConstraint("char_length(title) >= 1", name="event_promotions_title_ck"),
    )
    op.create_table(
        "event_promotion_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "promotion_id",
            sa.Integer(),
            sa.ForeignKey("event_promotions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("is_new_user", sa.Boolean(), nullable=False),
        sa.Column("first_opened_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("promotion_id", "user_id", name="uq_event_promotion_grants_user"),
    )
    op.create_index("ix_event_promotion_grants_user_id", "event_promotion_grants", ["user_id"])
    op.create_table(
        "event_promotion_scans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "promotion_id",
            sa.Integer(),
            sa.ForeignKey("event_promotions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "scanned_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('granted', 'existing_grant', 'outside_window')",
            name="event_promotion_scans_outcome_ck",
        ),
    )
    op.create_index(
        "ix_event_promotion_scans_promotion_id", "event_promotion_scans", ["promotion_id"]
    )
    op.add_column(
        "orders",
        sa.Column(
            "promotion_id",
            sa.Integer(),
            sa.ForeignKey(
                "event_promotions.id",
                name="orders_promotion_id_fkey",
                ondelete="RESTRICT",
            ),
        ),
    )
    op.create_index("ix_orders_promotion_id", "orders", ["promotion_id"])
    # A constant default: PostgreSQL 11+ adds it without rewriting existing rows.
    op.add_column(
        "order_items",
        sa.Column("promotion_discount", sa.Numeric(12, 2), server_default="0", nullable=False),
    )
    _restore(previous)


def downgrade() -> None:
    previous = _prepare()
    op.drop_column("order_items", "promotion_discount")
    op.drop_index("ix_orders_promotion_id", table_name="orders")
    op.drop_column("orders", "promotion_id")
    op.drop_index("ix_event_promotion_scans_promotion_id", table_name="event_promotion_scans")
    op.drop_table("event_promotion_scans")
    op.drop_index("ix_event_promotion_grants_user_id", table_name="event_promotion_grants")
    op.drop_table("event_promotion_grants")
    op.drop_table("event_promotions")
    _restore(previous)
