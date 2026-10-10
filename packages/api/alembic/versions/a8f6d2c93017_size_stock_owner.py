"""Add size balances and durable order payment ownership without operational stock."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a8f6d2c93017"
down_revision: str | Sequence[str] | None = "c4f8a921d730"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("size_cutover_blocked", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column("products", sa.Column("sizes_initialized_at", sa.DateTime(timezone=True)))
    op.create_table(
        "product_sizes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "product_id",
            sa.Integer(),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("size", sa.String(3), nullable=False),
        sa.Column("available_quantity", sa.Integer(), nullable=False),
        sa.Column("revision", sa.BigInteger(), server_default="0", nullable=False),
        sa.UniqueConstraint("product_id", "size", name="uq_product_sizes_product_size"),
        sa.CheckConstraint("available_quantity >= 0", name="product_sizes_quantity_ck"),
        sa.CheckConstraint("size IN ('L', 'XL', '2XL')", name="product_sizes_label_ck"),
        sa.CheckConstraint("revision >= 0", name="product_sizes_revision_ck"),
    )
    for name, type_ in (
        ("size_stock_protocol", sa.String(20)),
        ("size_owner_state", sa.String(20)),
        ("creation_token", sa.String(36)),
        ("cancellation_token", sa.String(36)),
        ("owned_invoice_uuid", sa.String(100)),
        ("checkout_url", sa.Text()),
        ("dispatched_at", sa.DateTime(timezone=True)),
        ("legacy_vest_creation_token", sa.String(36)),
    ):
        op.add_column("orders", sa.Column(name, type_, nullable=True))
    op.add_column(
        "orders", sa.Column("size_revision", sa.BigInteger(), server_default="0", nullable=False)
    )
    op.add_column(
        "orders",
        sa.Column("accepted_settlement", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_unique_constraint("uq_orders_owned_invoice_uuid", "orders", ["owned_invoice_uuid"])
    op.create_check_constraint(
        "orders_size_owner_ck",
        "orders",
        "size_stock_protocol IS NULL OR (size_stock_protocol = 'size_stock_v1' AND "
        "size_owner_state IS NOT NULL AND size_owner_state IN ('not_started', 'creating', 'live', 'cancelling', 'uncertain', "
        "'paid', 'cancelled', 'refunded'))",
    )
    op.add_column("order_items", sa.Column("size", sa.String(3), nullable=True))
    op.add_column("order_items", sa.Column("size_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_order_items_size_id",
        "order_items",
        "product_sizes",
        ["size_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    for name in ("allocated_quantity", "restored_quantity"):
        op.add_column(
            "order_items", sa.Column(name, sa.Integer(), server_default="0", nullable=False)
        )
    op.create_check_constraint(
        "order_items_restoration_ck",
        "order_items",
        "0 <= restored_quantity AND restored_quantity <= allocated_quantity",
    )
    op.create_table(
        "size_stock_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="RESTRICT")),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id", ondelete="RESTRICT")),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("actor", sa.String(200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_ref", sa.Text()),
        sa.Column("expected_revision", sa.BigInteger()),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    # Only an unused schema can be removed. Operational rollback must keep compatible code/data.
    active = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM orders WHERE size_stock_protocol IS NOT NULL) "
                "OR EXISTS (SELECT 1 FROM products WHERE sizes_initialized_at IS NOT NULL)"
            )
        )
        .scalar_one()
    )
    if active:
        raise RuntimeError("Size stock is active; restore a compatible application instead")
    op.drop_table("size_stock_records")
    op.drop_constraint("order_items_restoration_ck", "order_items", type_="check")
    op.drop_constraint("fk_order_items_size_id", "order_items", type_="foreignkey")
    for name in ("restored_quantity", "allocated_quantity", "size_id", "size"):
        op.drop_column("order_items", name)
    op.drop_constraint("orders_size_owner_ck", "orders", type_="check")
    op.drop_constraint("uq_orders_owned_invoice_uuid", "orders", type_="unique")
    for name in (
        "accepted_settlement",
        "size_revision",
        "legacy_vest_creation_token",
        "dispatched_at",
        "checkout_url",
        "owned_invoice_uuid",
        "cancellation_token",
        "creation_token",
        "size_owner_state",
        "size_stock_protocol",
    ):
        op.drop_column("orders", name)
    op.drop_table("product_sizes")
    op.drop_column("products", "sizes_initialized_at")
    op.drop_column("products", "size_cutover_blocked")
