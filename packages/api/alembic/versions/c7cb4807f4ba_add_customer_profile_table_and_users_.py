"""add customer_profile table and users.phone_verified_at

Second Stage-1 CRM table: 1:1 loyalty/CRM profile (survey + status/counters) next
to ``users``, plus a nullable ``users.phone_verified_at`` column. Additive — a
brand-new table + two enum types (gender, loyalty_status) not present on prod, and
one nullable column (safe for bot-side inserts). Authored via autogenerate against
a CLEAN parity DB, then reviewed by hand (enum DROPs added — alembic does not drop
the PG types with the table).

Revision ID: c7cb4807f4ba
Revises: 332db6094b1d
Create Date: 2026-06-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c7cb4807f4ba"
down_revision: Union[str, Sequence[str], None] = "332db6094b1d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customer_profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("gender", sa.Enum("MALE", "FEMALE", name="gender"), nullable=True),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("purchase_goal", sa.String(length=50), nullable=True),
        sa.Column("purchase_for_whom", sa.String(length=50), nullable=True),
        sa.Column(
            "status",
            sa.Enum("NEWBIE", "BIOTACT_FRIEND", name="loyalty_status"),
            server_default="NEWBIE",
            nullable=False,
        ),
        sa.Column("lifetime_activations", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_activation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_accrual_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_activated_sku", sa.String(length=32), nullable=True),
        sa.Column("registration_source", sa.String(length=20), nullable=True),
        sa.Column("survey_raw", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.add_column(
        "users", sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("users", "phone_verified_at")
    op.drop_table("customer_profile")
    # PG enum types are NOT auto-dropped with the table — drop them so down+up is clean.
    sa.Enum(name="loyalty_status").drop(op.get_bind())
    sa.Enum(name="gender").drop(op.get_bind())
