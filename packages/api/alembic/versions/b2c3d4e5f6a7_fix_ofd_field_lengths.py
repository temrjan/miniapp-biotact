"""fix_ofd_field_lengths

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2025-12-15 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Fix OFD field lengths: mxik=17, package_code=7."""
    op.alter_column('products', 'mxik',
                    existing_type=sa.String(length=20),
                    type_=sa.String(length=17),
                    existing_nullable=True)
    op.alter_column('products', 'package_code',
                    existing_type=sa.String(length=20),
                    type_=sa.String(length=7),
                    existing_nullable=True)


def downgrade() -> None:
    """Revert to original lengths."""
    op.alter_column('products', 'mxik',
                    existing_type=sa.String(length=17),
                    type_=sa.String(length=20),
                    existing_nullable=True)
    op.alter_column('products', 'package_code',
                    existing_type=sa.String(length=7),
                    type_=sa.String(length=20),
                    existing_nullable=True)
