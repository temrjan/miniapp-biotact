"""add_user_address_fields

Revision ID: d222800bda16
Revises: 82e3d42a6b87
Create Date: 2025-12-13 18:19:42.133304

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd222800bda16'
down_revision: Union[str, Sequence[str], None] = '82e3d42a6b87'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add address fields to users table."""
    op.add_column('users', sa.Column('address_street', sa.String(length=255), nullable=True))
    op.add_column('users', sa.Column('address_house', sa.String(length=20), nullable=True))
    op.add_column('users', sa.Column('address_apartment', sa.String(length=20), nullable=True))
    op.add_column('users', sa.Column('address_entrance', sa.String(length=10), nullable=True))
    op.add_column('users', sa.Column('address_floor', sa.String(length=10), nullable=True))
    op.add_column('users', sa.Column('address_comment', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('address_lat', sa.Float(), nullable=True))
    op.add_column('users', sa.Column('address_lng', sa.Float(), nullable=True))


def downgrade() -> None:
    """Remove address fields from users table."""
    op.drop_column('users', 'address_lng')
    op.drop_column('users', 'address_lat')
    op.drop_column('users', 'address_comment')
    op.drop_column('users', 'address_floor')
    op.drop_column('users', 'address_entrance')
    op.drop_column('users', 'address_apartment')
    op.drop_column('users', 'address_house')
    op.drop_column('users', 'address_street')
