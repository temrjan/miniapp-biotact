"""Capture-only expand/downgrade/up preserves baseline rows and legacy schema."""

import os
import subprocess
import sys
from uuid import uuid4

import psycopg2
from psycopg2 import sql

from src.config import settings


def test_capture_migration_preserves_baseline_and_has_metadata_parity():
    base_url = settings.database_url.rsplit("/", 1)[0]
    name = "g3a_migration_" + uuid4().hex
    admin = psycopg2.connect(base_url.replace("+asyncpg", "") + "/postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = base_url + "/" + name
    env = dict(os.environ, DATABASE_URL=url)

    def alembic(*args):
        # Arguments are fixed Alembic commands and our own interpreter.
        result = subprocess.run(  # noqa: S603 — fixed test-owned Alembic command
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
        with connection.cursor() as cur:
            cur.execute("""SELECT table_name,column_name,data_type,is_nullable,column_default
                FROM information_schema.columns WHERE table_schema='public'
                AND table_name NOT IN ('employee_referral_orders','employee_referral_order_items','alembic_version')
                ORDER BY table_name,ordinal_position""")
            columns = cur.fetchall()
            cur.execute("""SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public'
                AND tablename NOT IN ('employee_referral_orders','employee_referral_order_items','alembic_version')
                ORDER BY tablename,indexname""")
            indexes = cur.fetchall()
            cur.execute("""SELECT c.relname,con.conname,pg_get_constraintdef(con.oid) FROM pg_constraint con
                JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relname NOT IN
                ('employee_referral_orders','employee_referral_order_items','alembic_version') ORDER BY 1,2""")
            constraints = cur.fetchall()
            cur.execute("""SELECT tablename FROM pg_tables WHERE schemaname='public'
                AND tablename NOT IN ('employee_referral_orders','employee_referral_order_items','alembic_version') ORDER BY tablename""")
            tables = [row[0] for row in cur.fetchall()]
            rows = {}
            for table in tables:
                cur.execute(
                    sql.SQL(
                        "SELECT row_to_json(t)::text FROM {} t ORDER BY row_to_json(t)::text"
                    ).format(sql.Identifier(table))
                )
                rows[table] = cur.fetchall()
            return columns, indexes, constraints, rows

    try:
        alembic("upgrade", "72a6cf91b3d0")
        conn = psycopg2.connect(url.replace("+asyncpg", ""))
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO referrers (id,code,employee_name) VALUES (1,'G3ALEGACY','Synthetic legacy')"
                )
                cur.execute(
                    "INSERT INTO referral_invites (id,invite_code,used_by_referrer_id) VALUES (1,'G3AINVIT',1)"
                )
                cur.execute(
                    "INSERT INTO users (id,telegram_id,language_code,is_active,is_admin,referral_code) VALUES (1,990050,'ru',true,false,'G3ALEGACY')"
                )
                cur.execute(
                    "INSERT INTO products (id,name,slug,price,currency,stock_quantity,is_in_stock,sort_order,is_active,is_featured) VALUES (39,'Synthetic vest','capture-migration',10000,'UZS',10,true,0,true,false)"
                )
                cur.execute(
                    "INSERT INTO orders (id,order_number,user_id,status,subtotal,delivery_fee,total,currency,referral_code) VALUES (1,'CAPTURE-LEGACY',1,'PENDING',10000,0,10000,'UZS','G3ALEGACY')"
                )
                cur.execute(
                    "INSERT INTO order_items (id,order_id,product_id,product_name,product_price,quantity,total) VALUES (1,1,39,'Synthetic vest',10000,1,10000)"
                )
                cur.execute(
                    "INSERT INTO point_ledger (id,user_id,amount,reason) VALUES (1,1,10000,'adjust')"
                )
            before = fingerprint(conn)
            for direction, target in [
                ("upgrade", "head"),
                ("downgrade", "72a6cf91b3d0"),
                ("upgrade", "head"),
            ]:
                alembic(direction, target)
                assert fingerprint(conn) == before
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename IN ('employee_referral_orders','employee_referral_order_items')"
                    )
                    names = {row[0] for row in cur.fetchall()}
                    assert names == (
                        {"employee_referral_orders", "employee_referral_order_items"}
                        if direction == "upgrade"
                        else set()
                    )
                    if direction == "upgrade":
                        for table in names:
                            cur.execute(
                                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
                            )
                            assert cur.fetchone()[0] == 0, (
                                "Migration must not backfill historical attribution"
                            )
                if direction == "upgrade":
                    assert "No new upgrade operations detected" in alembic("check")
        finally:
            conn.close()
    finally:
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        admin.close()
