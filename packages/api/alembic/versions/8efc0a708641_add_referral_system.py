"""Add referral system tables and fields

Revision ID: 8efc0a708641
Revises: 08f2ab35492b
Create Date: 2026-02-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "8efc0a708641"
down_revision: Union[str, Sequence[str], None] = "08f2ab35492b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create referrers table
    op.create_table(
        "referrers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=10), nullable=False),
        sa.Column("employee_name", sa.String(length=255), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_referrers_code"), "referrers", ["code"], unique=True)
    op.create_index(op.f("ix_referrers_telegram_id"), "referrers", ["telegram_id"], unique=True)

    # Create referral_invites table
    op.create_table(
        "referral_invites",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invite_code", sa.String(length=10), nullable=False),
        sa.Column("is_used", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("used_by_referrer_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["used_by_referrer_id"], ["referrers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_referral_invites_invite_code"), "referral_invites", ["invite_code"], unique=True)

    # Add referral_code to users
    op.add_column("users", sa.Column("referral_code", sa.String(length=10), nullable=True))
    op.create_index(op.f("ix_users_referral_code"), "users", ["referral_code"])

    # Add referral_code to orders
    op.add_column("orders", sa.Column("referral_code", sa.String(length=10), nullable=True))
    op.create_index(op.f("ix_orders_referral_code"), "orders", ["referral_code"])


def downgrade() -> None:
    op.drop_index(op.f("ix_orders_referral_code"), table_name="orders")
    op.drop_column("orders", "referral_code")

    op.drop_index(op.f("ix_users_referral_code"), table_name="users")
    op.drop_column("users", "referral_code")

    op.drop_index(op.f("ix_referral_invites_invite_code"), table_name="referral_invites")
    op.drop_table("referral_invites")

    op.drop_index(op.f("ix_referrers_telegram_id"), table_name="referrers")
    op.drop_index(op.f("ix_referrers_code"), table_name="referrers")
    op.drop_table("referrers")
