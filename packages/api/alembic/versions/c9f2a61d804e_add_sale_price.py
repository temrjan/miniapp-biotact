"""Add public sale prices with an atomic price invariant."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9f2a61d804e"
down_revision: str | Sequence[str] | None = "b8e4f27c9a13"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products", sa.Column("is_sale", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column("products", sa.Column("sale_price", sa.Numeric(12, 2), nullable=True))
    op.create_check_constraint(
        "products_sale_ck",
        "products",
        "price > 0 AND (NOT is_sale OR (sale_price IS NOT NULL AND sale_price > 0 AND sale_price < price))",
    )


def downgrade() -> None:
    op.drop_constraint("products_sale_ck", "products", type_="check")
    op.drop_column("products", "sale_price")
    op.drop_column("products", "is_sale")
