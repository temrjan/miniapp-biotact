"""add_broadcasts_table

Revision ID: 224c920d41d0
Revises: 8efc0a708641
Create Date: 2026-03-11 13:51:59.931969

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "224c920d41d0"
down_revision: Union[str, Sequence[str], None] = "8efc0a708641"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create broadcasts table."""
    op.create_table(
        "broadcasts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("photo_url", sa.Text(), nullable=True),
        sa.Column("photo_file_id", sa.String(length=255), nullable=True),
        sa.Column("button_text", sa.String(length=64), nullable=True),
        sa.Column("button_url", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("total_users", sa.Integer(), nullable=False),
        sa.Column("sent_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("blocked_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_broadcasts_status", "broadcasts", ["status"])
    op.create_index(
        "idx_broadcasts_created_at",
        "broadcasts",
        [sa.text("created_at DESC")],
    )


def downgrade() -> None:
    """Drop broadcasts table."""
    op.drop_index("idx_broadcasts_created_at", table_name="broadcasts")
    op.drop_index("idx_broadcasts_status", table_name="broadcasts")
    op.drop_table("broadcasts")
