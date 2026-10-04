"""Owned PG17 transition, parity and app-role immutable history."""

import hashlib
import os
import subprocess
import sys
from uuid import uuid4

import psycopg2
import pytest
from psycopg2 import sql

from src.config import settings


def test_paid_additive_transition_and_app_role():
    base = settings.database_url.rsplit("/", 1)[0]
    name, role = "paid_migration_" + uuid4().hex, "paid_app_" + uuid4().hex
    admin = psycopg2.connect(base.replace("+asyncpg", "") + "/postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        cur.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER").format(sql.Identifier(role)))
    url = base + "/" + name
    connection = psycopg2.connect(url.replace("+asyncpg", ""))
    connection.autocommit = True

    def alembic(*args):
        result = subprocess.run(  # noqa: S603 - fixed executable and migration arguments
            [sys.executable, "-m", "alembic", *args],
            env=dict(os.environ, DATABASE_URL=url),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout + result.stderr

    def fingerprint():
        with connection.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename NOT IN ('alembic_version','employee_referral_paid_outbox') "
                "ORDER BY tablename"
            )
            tables = [r[0] for r in cur.fetchall()]
            rows = []
            for table in tables:
                cur.execute(
                    sql.SQL(
                        "SELECT row_to_json(t)::text FROM {} t ORDER BY row_to_json(t)::text"
                    ).format(sql.Identifier(table))
                )
                rows.append((table, cur.fetchall()))
            cur.execute(
                "SELECT table_name,column_name,data_type,is_nullable,column_default "
                "FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name NOT IN ('alembic_version','employee_referral_paid_outbox') "
                "ORDER BY table_name,ordinal_position"
            )
            return rows, cur.fetchall()

    try:
        alembic("upgrade", "9b47c6e3d821")
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO users(id,telegram_id,language_code,is_active,is_admin) "
                "VALUES(1,930001,'ru',true,false)"
            )
            cur.execute(
                "INSERT INTO orders(id,order_number,user_id,status,subtotal,delivery_fee,total,currency) "
                "VALUES(100,'PAID-SYNTHETIC',1,'PENDING',300,0,300,'UZS')"
            )
            cur.execute(
                "INSERT INTO order_items(id,order_id,product_name,product_price,quantity,total) "
                "VALUES(4201,100,'Synthetic',100,2,200),(4202,100,'Synthetic',100,1,100)"
            )
            cur.execute(
                "INSERT INTO employee_referral_orders(order_id,campaign_id,product_id,content_revision,"
                "campaign_name,employee_code,employee_display_name,captured_at,issued_at,valid_until,content_sha256,currency) "
                "VALUES(100,39,39,1,'Synthetic','00000000-0000-4000-8000-000000000001',"
                "'Synthetic',now(),now(),now()+interval '60 seconds',%s,'UZS')",
                ("0" * 64,),
            )
            cur.execute(
                "INSERT INTO employee_referral_order_items(order_item_id,order_id,product_id,product_name,"
                "quantity,unit_price,line_total) VALUES(4201,100,39,'Synthetic',2,100,200),"
                "(4202,100,39,'Synthetic',1,100,100)"
            )
            cur.execute("INSERT INTO point_ledger(user_id,amount,reason) VALUES(1,100,'adjust')")
        before = fingerprint()
        for action, target in (
            ("upgrade", "head"),
            ("downgrade", "9b47c6e3d821"),
            ("upgrade", "head"),
        ):
            alembic(action, target)
            assert fingerprint() == before
            if action == "upgrade":
                assert "No new upgrade operations detected" in alembic("check")
        body = b'{"synthetic":"paid-count"}'
        with connection.cursor() as cur:
            cur.execute(
                "GRANT USAGE ON SCHEMA public TO " + sql.Identifier(role).as_string(connection)
            )
            cur.execute(
                sql.SQL(
                    "GRANT SELECT,INSERT,UPDATE,DELETE ON employee_referral_paid_outbox TO {}"
                ).format(sql.Identifier(role))
            )
            cur.execute(
                sql.SQL(
                    "GRANT USAGE ON SEQUENCE employee_referral_paid_outbox_id_seq TO {}"
                ).format(sql.Identifier(role))
            )
            cur.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            cur.execute(
                "INSERT INTO employee_referral_paid_outbox(order_id,event_id,body,body_sha256,created_at) "
                "VALUES(100,%s,%s,%s,now())",
                (str(uuid4()), body, hashlib.sha256(body).hexdigest()),
            )
            for query in (
                "UPDATE employee_referral_paid_outbox SET body=body",
                "DELETE FROM employee_referral_paid_outbox",
            ):
                with pytest.raises(psycopg2.errors.CheckViolation):
                    cur.execute(query)
            cur.execute("RESET ROLE")
            cur.execute("DELETE FROM orders WHERE id=100")
            cur.execute("SELECT body FROM employee_referral_paid_outbox WHERE order_id=100")
            assert bytes(cur.fetchone()[0]) == body
            cur.execute("SELECT count(*) FROM employee_referral_orders")
            assert cur.fetchone()[0] == 0
        # Mandatory whole-chain scratch round-trip, without claiming history recovery.
        alembic("downgrade", "base")
        alembic("upgrade", "head")
        assert "No new upgrade operations detected" in alembic("check")
    finally:
        connection.close()
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            cur.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        admin.close()
