"""add consent table (loyalty)

First Stage-1 CRM table: per-purpose, versioned, timestamped user consent.
Additive — a brand-new table + enum type (not present on prod), so downgrade
safely drops both. Authored via autogenerate against a CLEAN parity DB, then
reviewed by hand (enum DROP added — alembic does not drop the PG type with the table).

Revision ID: 332db6094b1d
Revises: f1e2d3c4b5a6
Create Date: 2026-06-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "332db6094b1d"
down_revision: Union[str, Sequence[str], None] = "f1e2d3c4b5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "consent",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "purpose",
            sa.Enum("DATA_PROCESSING", "MARKETING", "PERSONALIZATION", name="consent_purpose"),
            nullable=False,
        ),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column(
            "granted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_consent_user_id"), "consent", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_consent_user_id"), table_name="consent")
    op.drop_table("consent")
    # PG enum type is NOT auto-dropped with the table — drop it so down+up is clean.
    sa.Enum(name="consent_purpose").drop(op.get_bind())
