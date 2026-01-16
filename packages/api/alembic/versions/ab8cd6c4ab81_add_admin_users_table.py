"""add_admin_users_table

Revision ID: ab8cd6c4ab81
Revises: ac6fa6753367
Create Date: 2026-01-05 10:55:45.713047

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'ab8cd6c4ab81'
down_revision: Union[str, Sequence[str], None] = 'ac6fa6753367'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('admin_users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('first_name', sa.String(length=100), nullable=True),
    sa.Column('last_name', sa.String(length=100), nullable=True),
    sa.Column('is_verified', sa.Boolean(), nullable=False),
    sa.Column('verification_token', sa.String(length=100), nullable=True),
    sa.Column('verification_token_expires', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reset_token', sa.String(length=100), nullable=True),
    sa.Column('reset_token_expires', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_login', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_admin_users_email'), 'admin_users', ['email'], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_admin_users_email'), table_name='admin_users')
    op.drop_table('admin_users')
