"""add_ofd_fields_to_products

Revision ID: a1b2c3d4e5f6
Revises: d222800bda16
Create Date: 2025-12-15 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'd222800bda16'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add OFD fields to products table for Multicard integration."""
    op.add_column('products', sa.Column('mxik', sa.String(length=20), nullable=True))
    op.add_column('products', sa.Column('package_code', sa.String(length=20), nullable=True))
    op.add_column('products', sa.Column('vat', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Remove OFD fields from products table."""
    op.drop_column('products', 'vat')
    op.drop_column('products', 'package_code')
    op.drop_column('products', 'mxik')
