"""stage 5: lottery_draws + lottery_tickets + customer_profile.is_adult (PR-5.1)

Revision ID: bfd3c849e8d1
Revises: b7d2f4a9c1e3
Create Date: 2026-07-24 13:43:50.895702

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bfd3c849e8d1"
down_revision: str | Sequence[str] | None = "b7d2f4a9c1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Stage 5 schema: the monthly draw and its frozen ticket snapshot.

    Additive only — two new tables plus one nullable-by-default column; no existing column is
    touched, so a code rollback leaves the schema harmless (matches the Stage-6 additive pattern).
    Everything stays dark behind ``loyalty_enabled``; no endpoints ship in PR-5.1.

    * ``lottery_draws`` — one row per calendar month (``period`` ``YYYY-MM``, UNIQUE). Fairness is
      commit-reveal: ``participant_hash`` (SHA-256 of the frozen ticket list) is published before
      the live random.org draw; ``status`` walks open→committed→drawn. Stored as String, not a PG
      enum — same convention as ``qr_codes.status`` (no enum type to create/drop).
    * ``lottery_tickets`` — the frozen snapshot. ``UNIQUE(draw_id, ticket_number)`` makes the
      sequential number the stable public identifier the hash covers. Both FKs ON DELETE CASCADE:
      deleting a draw drops its snapshot, deleting a user drops their entries — no money, no
      accounting witness here (unlike point_ledger), so CASCADE is safe.
    * ``customer_profile.is_adult`` — 18+ self-confirmation. ``server_default false`` + NOT NULL so
      the existing rows backfill without a data migration; eligibility grandfathers age>=18 in code.
    """
    op.create_table(
        "lottery_draws",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("period", sa.String(length=7), nullable=False),
        sa.Column("prize_label", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("total_tickets", sa.Integer(), server_default="0", nullable=False),
        sa.Column("participant_hash", sa.String(length=64), nullable=True),
        sa.Column("hash_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("drawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("period"),
    )
    op.create_table(
        "lottery_tickets",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("draw_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("ticket_number", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("is_winner", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("prize_rank", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["draw_id"], ["lottery_draws.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_lottery_tickets_draw_id"), "lottery_tickets", ["draw_id"], unique=False
    )
    op.create_index(
        op.f("ix_lottery_tickets_user_id"), "lottery_tickets", ["user_id"], unique=False
    )
    op.create_index(
        "uq_lottery_tickets_draw_number",
        "lottery_tickets",
        ["draw_id", "ticket_number"],
        unique=True,
    )
    op.add_column(
        "customer_profile",
        sa.Column("is_adult", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema — reverse order of upgrade."""
    op.drop_column("customer_profile", "is_adult")
    op.drop_index("uq_lottery_tickets_draw_number", table_name="lottery_tickets")
    op.drop_index(op.f("ix_lottery_tickets_user_id"), table_name="lottery_tickets")
    op.drop_index(op.f("ix_lottery_tickets_draw_id"), table_name="lottery_tickets")
    op.drop_table("lottery_tickets")
    op.drop_table("lottery_draws")
