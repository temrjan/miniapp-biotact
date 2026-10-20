"""Add dormant buyer email proofs and sessions; preserve canonical user history."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

revision: str = "c4f2a91e8d63"
down_revision: str | Sequence[str] | None = "b7e31c904a62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _prepare() -> tuple[str, str]:
    if context.is_offline_mode() or op.get_bind().dialect.name != "postgresql":
        raise RuntimeError("Buyer migration requires online PostgreSQL")
    bind = op.get_bind()
    previous = (
        bind.execute(sa.text("SHOW lock_timeout")).scalar_one(),
        bind.execute(sa.text("SHOW statement_timeout")).scalar_one(),
    )
    bind.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
    bind.execute(sa.text("SET LOCAL statement_timeout = '30s'"))
    return previous


def _restore(previous: tuple[str, str]) -> None:
    for name, value in zip(("lock_timeout", "statement_timeout"), previous, strict=True):
        op.get_bind().execute(
            sa.text("SELECT set_config(:name, :value, true)"), {"name": name, "value": value}
        )


def upgrade() -> None:
    previous = _prepare()
    op.alter_column("users", "telegram_id", existing_type=sa.BigInteger(), nullable=True)
    op.create_table(
        "buyer_email_identities",
        sa.Column("email", sa.String(254), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, unique=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "buyer_email_challenges",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("browser_digest", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("code_digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminal_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_buyer_email_challenges_expires_at", "buyer_email_challenges", ["expires_at"]
    )
    op.create_table(
        "buyer_sessions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("verifier_digest", sa.String(64), unique=True, nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        *(
            sa.Column(name, sa.DateTime(timezone=True), nullable=False)
            for name in (
                "created_at",
                "authenticated_at",
                "last_used_at",
                "absolute_expires_at",
                "idle_expires_at",
            )
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_buyer_sessions_idle_expires_at", "buyer_sessions", ["idle_expires_at"])
    op.create_table(
        "buyer_auth_limits",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("events", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_buyer_auth_limits_expires_at", "buyer_auth_limits", ["expires_at"])
    _restore(previous)


def downgrade() -> None:
    previous = _prepare()
    bind = op.get_bind()
    # Hold all affected relations until the outer Alembic transaction ends.
    bind.execute(
        sa.text(
            "LOCK TABLE users, buyer_email_identities, buyer_email_challenges, buyer_sessions, buyer_auth_limits IN ACCESS EXCLUSIVE MODE"
        )
    )
    for table in (
        "buyer_email_identities",
        "buyer_email_challenges",
        "buyer_sessions",
        "buyer_auth_limits",
    ):
        relation = sa.table(table)
        if bind.execute(sa.select(sa.exists().select_from(relation))).scalar_one():
            raise RuntimeError("Buyer downgrade refused: retained authentication state")
    if bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM users WHERE telegram_id IS NULL)")
    ).scalar_one():
        raise RuntimeError("Buyer downgrade refused: retained email-only customers")
    for table in (
        "buyer_auth_limits",
        "buyer_sessions",
        "buyer_email_challenges",
        "buyer_email_identities",
    ):
        op.drop_table(table)
    op.alter_column("users", "telegram_id", existing_type=sa.BigInteger(), nullable=False)
    _restore(previous)
