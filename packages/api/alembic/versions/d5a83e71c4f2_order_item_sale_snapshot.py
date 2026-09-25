"""Store sale terms on each order item."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5a83e71c4f2"
down_revision: str | Sequence[str] | None = "c9f2a61d804e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("order_items", sa.Column("is_sale_at_order", sa.Boolean(), nullable=True))
    op.add_column(
        "order_items", sa.Column("regular_price_at_order", sa.Numeric(12, 2), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("order_items", "regular_price_at_order")
    op.drop_column("order_items", "is_sale_at_order")
