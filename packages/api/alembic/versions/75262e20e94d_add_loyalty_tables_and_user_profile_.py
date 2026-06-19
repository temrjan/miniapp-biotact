"""add loyalty tables and user profile fields

Revision ID: 75262e20e94d
Revises: e1f2a3b4c5d6
Create Date: 2026-06-19 15:35:05.703108

Biotact Club phase 1 schema. Hand-written: autogenerate also surfaced
pre-existing model/DB drift (broadcasts/referrers tables not imported in
env.py, products.advantages/video_url, categories.is_purchasable, the orphan
referral_code columns) which is OUT of scope here and intentionally excluded.

PG enum labels follow the project convention = member NAMES (cf. lead_status).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "75262e20e94d"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

loyalty_status_enum = postgresql.ENUM(
    "NEWBIE", "BIOTACT_FRIEND", name="loyalty_status", create_type=False
)
draw_status_enum = postgresql.ENUM(
    "OPEN", "COMMITTED", "DRAWN", name="draw_status", create_type=False
)


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    loyalty_status_enum.create(bind, checkfirst=True)
    draw_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "code_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sku_code", sa.String(length=32), nullable=False),
        sa.Column("sku_name", sa.String(length=200), nullable=False),
        sa.Column("unit_price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("is_armed", sa.Boolean(), nullable=False),
        sa.Column("armed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_code_batches_is_armed"), "code_batches", ["is_armed"], unique=False)
    op.create_index(op.f("ix_code_batches_sku_code"), "code_batches", ["sku_code"], unique=False)

    op.create_table(
        "qr_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("batch_id", sa.Integer(), nullable=False),
        sa.Column("is_used", sa.Boolean(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_by_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["activated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["batch_id"], ["code_batches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_qr_codes_activated_by_user_id"),
        "qr_codes",
        ["activated_by_user_id"],
        unique=False,
    )
    op.create_index(op.f("ix_qr_codes_batch_id"), "qr_codes", ["batch_id"], unique=False)
    op.create_index(op.f("ix_qr_codes_code"), "qr_codes", ["code"], unique=True)
    op.create_index(op.f("ix_qr_codes_is_used"), "qr_codes", ["is_used"], unique=False)

    op.create_table(
        "point_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("qr_code_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["qr_code_id"], ["qr_codes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_point_transactions_created_at"),
        "point_transactions",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_point_transactions_reason"),
        "point_transactions",
        ["reason"],
        unique=False,
    )
    op.create_index(
        op.f("ix_point_transactions_user_id"),
        "point_transactions",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "survey_answers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("question_key", sa.String(length=50), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
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
        sa.UniqueConstraint("user_id", "question_key", name="uq_survey_user_question"),
    )
    op.create_index(op.f("ix_survey_answers_user_id"), "survey_answers", ["user_id"], unique=False)

    op.create_table(
        "lottery_draws",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("status", draw_status_enum, nullable=False),
        sa.Column("draw_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("commit_hash", sa.String(length=64), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("server_seed", sa.String(length=128), nullable=True),
        sa.Column("total_tickets", sa.Integer(), nullable=True),
        sa.Column("winner_user_id", sa.Integer(), nullable=True),
        sa.Column("drawn_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["winner_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_lottery_draws_period"), "lottery_draws", ["period"], unique=True)
    op.create_index(op.f("ix_lottery_draws_status"), "lottery_draws", ["status"], unique=False)

    op.create_table(
        "lottery_tickets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("draw_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("ticket_number", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["draw_id"], ["lottery_draws.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draw_id", "ticket_number", name="uq_ticket_number"),
        sa.UniqueConstraint("draw_id", "user_id", "source", name="uq_ticket_user_source"),
    )
    op.create_index(
        op.f("ix_lottery_tickets_draw_id"), "lottery_tickets", ["draw_id"], unique=False
    )
    op.create_index(
        op.f("ix_lottery_tickets_user_id"), "lottery_tickets", ["user_id"], unique=False
    )

    # products: link to QR SKU index (storefront, phase 2)
    op.add_column("products", sa.Column("sku_code", sa.String(length=32), nullable=True))
    op.create_index(op.f("ix_products_sku_code"), "products", ["sku_code"], unique=False)

    # users: loyalty + survey-derived profile fields (server_default backfills 462 rows)
    op.add_column(
        "users",
        sa.Column("balance", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("loyalty_status", loyalty_status_enum, server_default="NEWBIE", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("lifetime_activations", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "users", sa.Column("last_activation_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("users", sa.Column("gender", sa.String(length=10), nullable=True))
    op.add_column("users", sa.Column("age", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("city", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("purchase_goal", sa.String(length=50), nullable=True))
    op.add_column("users", sa.Column("purchase_for_whom", sa.String(length=50), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("users", "purchase_for_whom")
    op.drop_column("users", "purchase_goal")
    op.drop_column("users", "city")
    op.drop_column("users", "age")
    op.drop_column("users", "gender")
    op.drop_column("users", "last_activation_at")
    op.drop_column("users", "lifetime_activations")
    op.drop_column("users", "loyalty_status")
    op.drop_column("users", "balance")

    op.drop_index(op.f("ix_products_sku_code"), table_name="products")
    op.drop_column("products", "sku_code")

    op.drop_index(op.f("ix_lottery_tickets_user_id"), table_name="lottery_tickets")
    op.drop_index(op.f("ix_lottery_tickets_draw_id"), table_name="lottery_tickets")
    op.drop_table("lottery_tickets")
    op.drop_index(op.f("ix_lottery_draws_status"), table_name="lottery_draws")
    op.drop_index(op.f("ix_lottery_draws_period"), table_name="lottery_draws")
    op.drop_table("lottery_draws")
    op.drop_index(op.f("ix_survey_answers_user_id"), table_name="survey_answers")
    op.drop_table("survey_answers")
    op.drop_index(op.f("ix_point_transactions_user_id"), table_name="point_transactions")
    op.drop_index(op.f("ix_point_transactions_reason"), table_name="point_transactions")
    op.drop_index(op.f("ix_point_transactions_created_at"), table_name="point_transactions")
    op.drop_table("point_transactions")
    op.drop_index(op.f("ix_qr_codes_is_used"), table_name="qr_codes")
    op.drop_index(op.f("ix_qr_codes_code"), table_name="qr_codes")
    op.drop_index(op.f("ix_qr_codes_batch_id"), table_name="qr_codes")
    op.drop_index(op.f("ix_qr_codes_activated_by_user_id"), table_name="qr_codes")
    op.drop_table("qr_codes")
    op.drop_index(op.f("ix_code_batches_sku_code"), table_name="code_batches")
    op.drop_index(op.f("ix_code_batches_is_armed"), table_name="code_batches")
    op.drop_table("code_batches")

    bind = op.get_bind()
    draw_status_enum.drop(bind, checkfirst=True)
    loyalty_status_enum.drop(bind, checkfirst=True)
