"""Add the consultant journal tables (spec.opyt r2 JT-01 to JT-03).

Expand-only: creates two new tables and their index; no existing object is changed.

Revision ID: 8d1b1791159b
Revises: e9a21c40b735
Create Date: 2026-11-03
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "8d1b1791159b"
down_revision = "e9a21c40b735"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consultant_conversation",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("tab_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_epoch", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_turn_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tab_id", "token_epoch"),
    )
    op.create_table(
        "consultant_turn",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column(
            "conversation_id",
            sa.BigInteger(),
            sa.ForeignKey("consultant_conversation.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("tab_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "written_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("fault_code", sa.Text(), nullable=True),
        sa.Column("response_status", sa.Text(), nullable=True),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("graf_outcome", sa.Text(), nullable=True),
        sa.Column("situation", sa.Text(), nullable=True),
        sa.Column("template", sa.Text(), nullable=True),
        sa.Column(
            "offered_product_ids",
            postgresql.ARRAY(sa.Integer()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("jev", postgresql.JSONB(), nullable=True),
        sa.Column("luna", postgresql.JSONB(), nullable=True),
        sa.Column("facts", postgresql.JSONB(), nullable=True),
        sa.Column("versions", postgresql.JSONB(), nullable=True),
        sa.Column("asked_question", postgresql.JSONB(), nullable=True),
        sa.Column("resolution", postgresql.JSONB(), nullable=True),
        sa.Column("tokens", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("agreed_situation", sa.Text(), nullable=True),
        sa.Column("agreed_source", sa.Text(), nullable=True),
        sa.Column("opyt", postgresql.JSONB(), nullable=True),
        sa.Column("memory", postgresql.JSONB(), nullable=True),
        sa.CheckConstraint("agreed_source IN ('jev_luna','confirmed')"),
    )
    op.create_index(
        "ix_consultant_turn_conversation_id_id", "consultant_turn", ["conversation_id", "id"]
    )


def downgrade() -> None:
    # Pattern of e9a21c40b735: empty disposable schemas round-trip; stored turns are never
    # dropped by a downgrade.
    connection = op.get_bind()
    for name in ("consultant_turn", "consultant_conversation"):
        if connection.scalar(sa.select(sa.exists().select_from(sa.table(name)))):
            raise RuntimeError("Consultant journal downgrade refused: the journal holds rows")
    op.drop_table("consultant_turn")
    op.drop_table("consultant_conversation")
