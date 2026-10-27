"""Native S3 schema preservation, timeout, parity and populated rollback refusal."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import psycopg2
import pytest
from psycopg2 import sql

from tests import test_category_schema_migration as migration

scratch = migration.scratch
PREVIOUS = "b7e31c904a62"
CURRENT = "d8a14f90bc21"
BUYER_TABLES = {
    "buyer_email_identities",
    "buyer_email_challenges",
    "buyer_sessions",
    "buyer_auth_limits",
    "buyer_telegram_requests",
}


def preserved(db):
    tables = db.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
    )
    data = []
    for (name,) in tables:
        if name in BUYER_TABLES or name == "alembic_version" or name.startswith("consultant_"):
            continue
        rows = db.execute(
            sql.SQL("SELECT row_to_json(t)::text FROM {} t ORDER BY 1").format(sql.Identifier(name))
        )
        data.append((name, rows))
    columns = db.execute("""
        SELECT c.relname,a.attname,a.atttypid,a.atttypmod,
          CASE WHEN c.relname='users' AND a.attname='telegram_id' THEN NULL ELSE a.attnotnull END,
          a.attidentity,a.attgenerated,pg_get_expr(d.adbin,d.adrelid)
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
        LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
        WHERE n.nspname='public' AND c.relkind <> 'i' AND c.relname NOT LIKE 'buyer_%' AND c.relname NOT LIKE 'consultant_%' ORDER BY c.relname,a.attnum
    """)
    constraints = db.execute("""
        SELECT c.conrelid::regclass::text,c.conname,pg_get_constraintdef(c.oid)
        FROM pg_constraint c JOIN pg_namespace n ON n.oid=c.connamespace
        WHERE n.nspname='public' AND c.conrelid::regclass::text NOT LIKE 'buyer_%' AND c.conrelid::regclass::text NOT LIKE 'consultant_%'
        ORDER BY 1,2
    """)
    indexes = db.execute(
        "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename NOT LIKE 'buyer_%' AND tablename NOT LIKE 'consultant_%' ORDER BY 1,2"
    )
    return data, columns, constraints, indexes


def check_parity(db):
    # Historical migration tests compare their exact predecessor/successor;
    # current metadata parity is checked at the complete current chain.
    previous = db.marker()
    db.migrate(target="head")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=migration.API_ROOT,
        env={**os.environ, "DATABASE_URL": db.url},
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if db.marker() != previous:
        db.migrate("downgrade", previous)


def test_predecessor_preservation_native_null_unique_fk_and_parity(scratch):
    scratch.migrate(target=PREVIOUS)
    scratch.seed()
    before = preserved(scratch)
    scratch.migrate(target=CURRENT)
    assert scratch.marker() == CURRENT
    assert preserved(scratch) == before
    assert scratch.execute(
        "SELECT attnotnull FROM pg_attribute WHERE attrelid='users'::regclass AND attname='telegram_id'"
    ) == [(False,)]
    scratch.execute(
        "INSERT INTO users (id,telegram_id,language_code,is_active,is_admin) VALUES (801,NULL,'ru',true,false),(802,NULL,'ru',true,false)"
    )
    with pytest.raises(psycopg2.errors.UniqueViolation):
        scratch.execute(
            "INSERT INTO users (telegram_id,language_code,is_active,is_admin) VALUES (990701,'ru',true,false)"
        )
    scratch.execute("INSERT INTO buyer_email_identities VALUES ('a@example.test',801,now())")
    for statement, error in (
        (
            "INSERT INTO buyer_email_identities VALUES ('a@example.test',802,now())",
            psycopg2.errors.UniqueViolation,
        ),
        (
            "INSERT INTO buyer_email_identities VALUES ('b@example.test',801,now())",
            psycopg2.errors.UniqueViolation,
        ),
        (
            "INSERT INTO buyer_email_identities VALUES ('c@example.test',9999,now())",
            psycopg2.errors.ForeignKeyViolation,
        ),
    ):
        with pytest.raises(error):
            scratch.execute(statement)
    scratch.migrate(target="head")  # Model parity is defined at the current head (G1 after TG1).
    check_parity(scratch)


def test_clean_chain_and_empty_roundtrip(scratch):
    scratch.migrate(target="head")
    check_parity(scratch)

    scratch.migrate("downgrade", "base")
    assert scratch.execute("SELECT version_num FROM alembic_version") == []
    scratch.migrate(target="head")
    check_parity(scratch)
    scratch.migrate("downgrade", PREVIOUS)
    assert scratch.marker() == PREVIOUS
    assert not scratch.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'buyer_%'"
    )
    scratch.migrate(target="head")
    check_parity(scratch)


@pytest.mark.parametrize("state", ["identity", "challenge", "session", "limit", "null_user"])
def test_populated_downgrade_refuses_before_any_change(scratch, state):
    scratch.migrate(target=CURRENT)
    scratch.execute(
        "INSERT INTO users(id,telegram_id,language_code,is_active,is_admin) VALUES(701,990701,'ru',true,false)"
    )
    statements = {
        "identity": "INSERT INTO buyer_email_identities VALUES('a@example.test',701,now())",
        "challenge": "INSERT INTO buyer_email_challenges(id,email,browser_digest,purpose,code_digest,state,attempts,created_at,expires_at) VALUES('handle','a@example.test','digest','sign_in','digest','sending',0,now(),now())",
        "session": "INSERT INTO buyer_sessions(id,user_id,verifier_digest,generation,created_at,authenticated_at,last_used_at,absolute_expires_at,idle_expires_at) VALUES('handle',701,'digest',1,now(),now(),now(),now(),now())",
        "limit": "INSERT INTO buyer_auth_limits VALUES('key','[]',now())",
        "null_user": "UPDATE users SET telegram_id=NULL WHERE id=701",
    }
    scratch.execute(statements[state])
    before = scratch.snapshot()
    assert "Buyer downgrade refused" in scratch.migrate("downgrade", PREVIOUS, success=False)
    assert scratch.marker() == CURRENT
    assert scratch.snapshot() == before
    assert (
        len(
            scratch.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'buyer_%'"
            )
        )
        == 5
    )


def test_s3_lock_timeout_has_no_partial_schema(scratch):
    scratch.migrate(target=PREVIOUS)
    before = scratch.snapshot()
    with migration.blocker(scratch) as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        with blocker.cursor() as cur:
            cur.execute("LOCK TABLE users IN ACCESS EXCLUSIVE MODE")
        future = pool.submit(scratch.migrate, target="head", success=False)

        def waiting():
            return scratch.execute(
                "SELECT pid FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE 'ALTER TABLE users%'"
            )

        scratch.wait_for(waiting, "S3 migration waiting on synthetic users lock")
        assert "lock timeout" in future.result(timeout=15)
    assert scratch.snapshot() == before
    assert scratch.marker() == PREVIOUS
