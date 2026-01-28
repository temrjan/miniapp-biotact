"""Add CRM tables (leads, family_members) and extend users

Revision ID: 08f2ab35492b
Revises: ab8cd6c4ab81
Create Date: 2026-01-26 10:03:26.565319

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '08f2ab35492b'
down_revision: Union[str, Sequence[str], None] = 'ab8cd6c4ab81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create family_members table
    op.create_table('family_members',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('relation', sa.String(length=100), nullable=True),
        sa.Column('age', sa.Integer(), nullable=True),
        sa.Column('symptoms', postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_family_members_user_id'), 'family_members', ['user_id'], unique=False)

    # Create leads table
    op.create_table('leads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False),
        sa.Column('status', sa.Enum('NEW', 'IN_PROGRESS', 'CONVERTED', 'CLOSED', name='lead_status', create_type=True), nullable=False),
        sa.Column('request_text', sa.Text(), nullable=True),
        sa.Column('ai_summary', sa.Text(), nullable=True),
        sa.Column('recommended_products', postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column('manager_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_leads_created_at'), 'leads', ['created_at'], unique=False)
    op.create_index(op.f('ix_leads_status'), 'leads', ['status'], unique=False)
    op.create_index(op.f('ix_leads_telegram_id'), 'leads', ['telegram_id'], unique=False)
    op.create_index(op.f('ix_leads_user_id'), 'leads', ['user_id'], unique=False)

    # Add CRM columns to users table
    op.add_column('users', sa.Column('symptoms', postgresql.ARRAY(sa.Text()), nullable=True))
    op.add_column('users', sa.Column('ai_notes', sa.Text(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    # Remove CRM columns from users
    op.drop_column('users', 'ai_notes')
    op.drop_column('users', 'symptoms')

    # Drop leads table
    op.drop_index(op.f('ix_leads_user_id'), table_name='leads')
    op.drop_index(op.f('ix_leads_telegram_id'), table_name='leads')
    op.drop_index(op.f('ix_leads_status'), table_name='leads')
    op.drop_index(op.f('ix_leads_created_at'), table_name='leads')
    op.drop_table('leads')

    # Drop lead_status enum
    op.execute('DROP TYPE IF EXISTS lead_status')

    # Drop family_members table
    op.drop_index(op.f('ix_family_members_user_id'), table_name='family_members')
    op.drop_table('family_members')
