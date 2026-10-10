"""Upgrade/down/up parity and preservation on a newly owned scratch database."""

import json
import os
import subprocess
import sys
from uuid import uuid4

import psycopg2
from psycopg2 import sql

from src.config import settings


def test_additive_migration_preserves_legacy_objects_rows_and_parity():
    base_url = settings.database_url.rsplit("/", 1)[0]
    name = "k1_migration_" + uuid4().hex
    admin = psycopg2.connect(base_url.replace("+asyncpg", "") + "/postgres")
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = base_url + "/" + name
    env = dict(os.environ, DATABASE_URL=url)

    def alembic(*args):
        result = subprocess.run(  # noqa: S603 — executable and Alembic arguments are fixed by this test
            [sys.executable, "-m", "alembic", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout + result.stderr

    def fingerprint(connection):
        # Compare the legacy schema, excluding the independently tested additive
        # credentials table; alembic check below still covers all current models.
        with connection.cursor() as cursor:
            cursor.execute("""SELECT tablename, indexname, indexdef FROM pg_indexes
                WHERE schemaname='public' AND tablename NOT LIKE 'employee_referral_%' AND tablename <> 'admin_password_accounts'
                ORDER BY tablename, indexname""")
            indexes = cursor.fetchall()
            cursor.execute("""SELECT table_name, column_name, data_type, is_nullable, column_default
                FROM information_schema.columns WHERE table_schema='public'
                AND table_name NOT LIKE 'employee_referral_%' AND table_name <> 'admin_password_accounts' ORDER BY table_name, ordinal_position""")
            columns = cursor.fetchall()
            cursor.execute("""SELECT c.relname, con.conname, pg_get_constraintdef(con.oid)
                FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
                AND c.relname NOT LIKE 'employee_referral_%' AND c.relname <> 'admin_password_accounts' ORDER BY 1,2""")
            constraints = cursor.fetchall()
            rows = {}
            for table in ("referrers", "referral_invites", "users", "orders", "point_ledger"):
                cursor.execute(
                    sql.SQL("SELECT row_to_json(t)::text FROM {} t ORDER BY id").format(
                        sql.Identifier(table)
                    )
                )
                rows[table] = cursor.fetchall()
        return indexes, columns, constraints, rows

    try:
        alembic("upgrade", "d5a83e71c4f2")
        connection = psycopg2.connect(url.replace("+asyncpg", ""))
        connection.autocommit = True
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO referrers (id,code,employee_name) VALUES (1,'K1LEGACY','Synthetic legacy')"
                )
                cursor.execute(
                    "INSERT INTO referral_invites (id,invite_code,used_by_referrer_id) VALUES (1,'K1INVITE',1)"
                )
                cursor.execute("""INSERT INTO users (id,telegram_id,language_code,is_active,is_admin,referral_code)
                    VALUES (1,910001,'ru',true,false,'K1LEGACY')""")
                cursor.execute("""INSERT INTO orders (id,order_number,user_id,status,subtotal,delivery_fee,total,currency,referral_code)
                    VALUES (1,'K1-LEGACY',1,'PENDING',1000,0,1000,'UZS','K1LEGACY')""")
                cursor.execute(
                    "INSERT INTO point_ledger (id,user_id,amount,reason) VALUES (1,1,100,'adjust')"
                )
            before = fingerprint(connection)
            for direction in ("upgrade", "downgrade", "upgrade"):
                target = "72a6cf91b3d0" if direction == "upgrade" else "d5a83e71c4f2"
                alembic(direction, target)
                assert fingerprint(connection) == before
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE 'employee_referral_%'"
                    )
                    names = {row[0] for row in cursor.fetchall()}
                assert names == (
                    {
                        "employee_referral_campaigns",
                        "employee_referral_tokens",
                        "employee_referral_k1_receipts",
                    }
                    if direction == "upgrade"
                    else set()
                )
                if direction == "upgrade":
                    # K1's historical head remains independently reversible; parity is
                    # defined against all current models, including later additive tables.
                    alembic("upgrade", "c4f8a921d730")
                    assert fingerprint(connection) == before
                    alembic("upgrade", "head")
                    assert "No new upgrade operations detected" in alembic("check")
                    # Later size migration adds columns/tables intentionally. Every
                    # preexisting row field must still match, and downgrade must
                    # restore the exact pre-size legacy schema and data below.
                    current_rows = fingerprint(connection)[3]
                    for table, old_rows in before[3].items():
                        assert len(current_rows[table]) == len(old_rows)
                        for old, current in zip(old_rows, current_rows[table], strict=True):
                            original, actual = json.loads(old[0]), json.loads(current[0])
                            assert {key: actual[key] for key in original} == original
                    alembic("downgrade", "c4f8a921d730")
                    assert fingerprint(connection) == before
                    alembic("downgrade", "72a6cf91b3d0")
            print(
                "K1 baseline -> new -> baseline -> new: legacy rows/objects unchanged; alembic check passed twice"
            )
        finally:
            connection.close()
    finally:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        admin.close()


def test_model_import_is_standalone():
    result = subprocess.run(
        [sys.executable, "-c", "import src.employee_referrals.models"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
