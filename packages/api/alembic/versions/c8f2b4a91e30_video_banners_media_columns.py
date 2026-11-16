"""Add video and mobile media columns to banners.

Revision ID: c8f2b4a91e30
Revises: 87688cb444f4
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8f2b4a91e30"
down_revision: str | Sequence[str] | None = "87688cb444f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable mobile image and clip URL columns to banners."""
    op.add_column("banners", sa.Column("image_mobile_url", sa.String(length=500), nullable=True))
    op.add_column("banners", sa.Column("video_url", sa.String(length=500), nullable=True))
    op.add_column("banners", sa.Column("video_mobile_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    """Drop mobile image and clip URL columns from banners."""
    op.drop_column("banners", "video_mobile_url")
    op.drop_column("banners", "video_url")
    op.drop_column("banners", "image_mobile_url")
