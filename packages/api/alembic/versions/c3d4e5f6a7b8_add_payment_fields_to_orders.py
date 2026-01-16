"""add_payment_fields_to_orders

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2025-12-15 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add payment fields to orders table for Multicard integration."""
    op.add_column('orders', sa.Column('payment_uuid', sa.String(length=100), nullable=True))
    op.add_column('orders', sa.Column('payment_status', sa.String(length=20), nullable=True, server_default='unpaid'))
    op.add_column('orders', sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True))

    # Create index on payment_uuid
    op.create_index('ix_orders_payment_uuid', 'orders', ['payment_uuid'])


def downgrade() -> None:
    """Remove payment fields from orders table."""
    op.drop_index('ix_orders_payment_uuid', table_name='orders')
    op.drop_column('orders', 'paid_at')
    op.drop_column('orders', 'payment_status')
    op.drop_column('orders', 'payment_uuid')
