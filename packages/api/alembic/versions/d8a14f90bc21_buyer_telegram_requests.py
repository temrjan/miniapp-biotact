"""Add dormant, context-bound Telegram buyer approvals."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "d8a14f90bc21"
down_revision: str | Sequence[str] | None = "c4f2a91e8d63"
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
    op.create_table(
        "buyer_telegram_requests",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("browser_digest", sa.String(64), nullable=False),
        sa.Column("request_key", sa.String(32), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminal_at", sa.DateTime(timezone=True)),
        sa.Column("expected_actor", sa.BigInteger()),
        sa.Column("deciding_actor", sa.BigInteger()),
        sa.Column("decision", sa.String(8)),
        sa.Column("delivery", sa.String(16), nullable=False),
        sa.Column("completed_user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column(
            "completed_session_id",
            sa.String(32),
            sa.ForeignKey("buyer_sessions.id", ondelete="SET NULL"),
        ),
        sa.Column("completed_generation", sa.Integer()),
        sa.UniqueConstraint("browser_digest", "request_key", name="uq_buyer_telegram_browser_key"),
    )
    op.create_index(
        "ix_buyer_telegram_requests_browser_digest", "buyer_telegram_requests", ["browser_digest"]
    )
    _restore(previous)


def downgrade() -> None:
    previous = _prepare()
    op.get_bind().execute(sa.text("LOCK TABLE buyer_telegram_requests IN ACCESS EXCLUSIVE MODE"))
    if (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM buyer_telegram_requests)"))
        .scalar_one()
    ):
        raise RuntimeError("Populated buyer Telegram downgrade refused; repair forward")
    op.drop_table("buyer_telegram_requests")
    _restore(previous)
