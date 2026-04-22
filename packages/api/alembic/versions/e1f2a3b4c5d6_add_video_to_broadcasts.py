"""add_video_to_broadcasts

Revision ID: e1f2a3b4c5d6
Revises: 224c920d41d0
Create Date: 2026-04-22 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "224c920d41d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add video_url and video_file_id columns to broadcasts table."""
    op.add_column("broadcasts", sa.Column("video_url", sa.Text(), nullable=True))
    op.add_column(
        "broadcasts",
        sa.Column("video_file_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    """Remove video columns from broadcasts table."""
    op.drop_column("broadcasts", "video_file_id")
    op.drop_column("broadcasts", "video_url")
