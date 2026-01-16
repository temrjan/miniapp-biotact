"""Add additional_info and disclaimer fields to products

Revision ID: ac6fa6753367
Revises: c3d4e5f6a7b8
Create Date: 2025-12-25 07:43:03.710772

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ac6fa6753367'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Add additional_info_title columns
    op.add_column('products', sa.Column('additional_info_title', sa.String(200), nullable=True))
    op.add_column('products', sa.Column('additional_info_title_uz', sa.String(200), nullable=True))
    
    # Add additional_info columns
    op.add_column('products', sa.Column('additional_info', sa.Text(), nullable=True))
    op.add_column('products', sa.Column('additional_info_uz', sa.Text(), nullable=True))
    
    # Add disclaimer columns
    op.add_column('products', sa.Column('disclaimer', sa.Text(), nullable=True))
    op.add_column('products', sa.Column('disclaimer_uz', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Remove columns in reverse order
    op.drop_column('products', 'disclaimer_uz')
    op.drop_column('products', 'disclaimer')
    op.drop_column('products', 'additional_info_uz')
    op.drop_column('products', 'additional_info')
    op.drop_column('products', 'additional_info_title_uz')
    op.drop_column('products', 'additional_info_title')
