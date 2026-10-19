"""Reconcile the existing category boolean without rewriting application data."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = "b7e31c904a62"
down_revision: str | Sequence[str] | None = "a8f6d2c93017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_oid() -> int:
    row = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT c.oid, c.relkind = 'r' AND c.relpersistence = 'p' "
                "AND NOT c.relispartition AND c.reloftype = 0 "
                "AND NOT c.relrowsecurity "
                "AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i "
                "WHERE i.inhrelid = c.oid OR i.inhparent = c.oid) AS supported "
                "FROM pg_catalog.pg_class c "
                "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = 'categories'"
            )
        )
        .one_or_none()
    )
    if row is None or not row.supported:
        raise RuntimeError("S1 schema preflight: expected an ordinary standalone public.categories")
    return int(row.oid)


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("S1 schema preflight requires an online PostgreSQL transaction")
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("S1 schema preflight requires PostgreSQL")
    if bind.execute(sa.text("SHOW transaction_isolation")).scalar_one() != "read committed":
        raise RuntimeError("S1 schema preflight requires READ COMMITTED")

    previous_lock = bind.execute(sa.text("SHOW lock_timeout")).scalar_one()
    previous_statement = bind.execute(sa.text("SHOW statement_timeout")).scalar_one()
    bind.execute(sa.text("SELECT pg_catalog.set_config('lock_timeout', '5s', true)"))
    bind.execute(sa.text("SELECT pg_catalog.set_config('statement_timeout', '30s', true)"))
    original_oid = _table_oid()
    bind.execute(sa.text("LOCK TABLE ONLY public.categories IN ACCESS EXCLUSIVE MODE"))
    if _table_oid() != original_oid:
        raise RuntimeError("S1 schema preflight: public.categories changed while acquiring lock")
    supported_column = bind.execute(
        sa.text(
            "SELECT a.atttypid = 'pg_catalog.bool'::pg_catalog.regtype "
            "AND a.attgenerated = '' AND a.attidentity = '' "
            "FROM pg_catalog.pg_attribute a "
            "WHERE a.attrelid = :oid AND a.attname = 'is_purchasable' "
            "AND a.attnum > 0 AND NOT a.attisdropped"
        ),
        {"oid": original_oid},
    ).scalar_one_or_none()
    if not supported_column:
        raise RuntimeError("S1 schema preflight: expected native boolean is_purchasable")
    if bind.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM public.categories WHERE is_purchasable IS NULL)")
    ).scalar_one():
        raise RuntimeError(
            "S1 NULL preflight: category purchase flags require an explicit repair decision"
        )

    # One DDL statement, no row repair; the enclosing Alembic transaction owns commit/rollback.
    bind.execute(
        sa.text(
            "ALTER TABLE ONLY public.categories "
            "ALTER COLUMN is_purchasable SET DEFAULT true, "
            "ALTER COLUMN is_purchasable SET NOT NULL"
        )
    )
    for name, value in (("lock_timeout", previous_lock), ("statement_timeout", previous_statement)):
        bind.execute(
            sa.text("SELECT pg_catalog.set_config(:name, :value, true)"),
            {"name": name, "value": value},
        )


def downgrade() -> None:
    # Compatible code rollback retains the stronger schema and all existing values.
    pass
