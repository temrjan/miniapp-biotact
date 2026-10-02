"""Add the default-off K1 registry, identities and receipts.

Revision ID: 72a6cf91b3d0
Revises: d5a83e71c4f2
"""
from alembic import op
import sqlalchemy as sa

revision = "72a6cf91b3d0"
down_revision = "d5a83e71c4f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "employee_referral_campaigns",
        sa.Column("campaign_id", sa.BigInteger(), nullable=False, autoincrement=False),
        sa.Column("content_revision", sa.BigInteger(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("state", sa.String(9), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("campaign_id", name="pk_employee_referral_campaigns"),
        sa.CheckConstraint("campaign_id > 0 AND product_id > 0 AND content_revision > 0", name="ck_employee_referral_campaigns_positive"),
        sa.CheckConstraint("state IN ('published', 'ended')", name="ck_employee_referral_campaigns_state"),
        sa.CheckConstraint("end_at > start_at", name="ck_employee_referral_campaigns_window"),
        sa.CheckConstraint("(state = 'published' AND ended_at IS NULL) OR (state = 'ended' AND ended_at IS NOT NULL AND published_at <= ended_at)", name="ck_employee_referral_campaigns_end"),
        sa.CheckConstraint("valid_until = issued_at + interval '60 seconds'", name="ck_employee_referral_campaigns_lease"),
    )
    op.create_table(
        "employee_referral_tokens",
        sa.Column("token", sa.String(22), nullable=False),
        sa.Column("campaign_id", sa.BigInteger(), nullable=False),
        sa.Column("employee_code", sa.Uuid(), nullable=False),
        sa.Column("employee_display_name", sa.String(161), nullable=False),
        sa.Column("in_current_registry", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("token", name="pk_employee_referral_tokens"),
        sa.ForeignKeyConstraint(["campaign_id"], ["employee_referral_campaigns.campaign_id"], name="fk_employee_referral_tokens_campaign", ondelete="RESTRICT"),
        sa.UniqueConstraint("campaign_id", "employee_code", name="uq_employee_referral_tokens_pair"),
    )
    op.create_table(
        "employee_referral_k1_receipts",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.BigInteger(), nullable=False),
        sa.Column("content_revision", sa.BigInteger(), nullable=False),
        sa.Column("body_sha256", sa.String(64), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(10), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name="pk_employee_referral_k1_receipts"),
        sa.ForeignKeyConstraint(["campaign_id"], ["employee_referral_campaigns.campaign_id"], name="fk_employee_referral_k1_receipts_campaign", ondelete="RESTRICT"),
        sa.CheckConstraint("content_revision > 0", name="ck_employee_referral_k1_receipts_revision"),
        sa.CheckConstraint("outcome IN ('applied', 'renewed', 'unchanged', 'superseded')", name="ck_employee_referral_k1_receipts_outcome"),
    )
    op.create_index("ix_employee_referral_k1_receipts_revision", "employee_referral_k1_receipts", ["campaign_id", "content_revision"])


def downgrade() -> None:
    op.drop_table("employee_referral_k1_receipts")
    op.drop_table("employee_referral_tokens")
    op.drop_table("employee_referral_campaigns")
