"""Populated paid transition, app-role lock/sequence/immutability and safe refusal."""

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from uuid import uuid4

import psycopg2
import pytest
from psycopg2 import sql

from src.config import settings
from src.employee_referrals.return_schemas import CountSnapshot


def test_return_additive_shared_sequence_and_restricted_role():
    base = settings.database_url.rsplit("/", 1)[0]
    name, role = "return_migration_" + uuid4().hex, "return_app_" + uuid4().hex
    admin = psycopg2.connect(base.replace("+asyncpg", "") + "/postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        cur.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER").format(sql.Identifier(role)))
    url = base + "/" + name
    connection = psycopg2.connect(url.replace("+asyncpg", ""))
    connection.autocommit = True

    def alembic(*args, ok=True):
        result = subprocess.run(  # noqa: S603 - fixed interpreter/Alembic on owned scratch DB
            [sys.executable, "-m", "alembic", *args],
            env=dict(os.environ, DATABASE_URL=url),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert (result.returncode == 0) is ok, result.stdout + result.stderr
        return result.stdout + result.stderr

    def paid_fingerprint():
        with connection.cursor() as cur:
            cur.execute(
                "SELECT row_to_json(t)::text FROM employee_referral_paid_outbox t ORDER BY id"
            )
            return cur.fetchall()

    try:
        alembic("upgrade", "b5a1d6e8f230")
        # Root's bytes/metadata match a real retained paid shape, without source-order FK.
        now = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        paid_id = str(uuid4())
        value = {
            "schema_version": 1,
            "event_id": paid_id,
            "event_type": "sale_paid",
            "source": "biotact-shop",
            "order_id": 100,
            "order_version": 1,
            "source_position": 1,
            "occurred_at": now,
            "source_recorded_at": now,
            "attribution": {
                "campaign_id": 1,
                "employee_code": "22222222-2222-4222-8222-222222222222",
                "product_id": 39,
                "content_revision": 1,
                "captured_at": now,
            },
            "lines": [{"order_item_id": 4201, "product_id": 39, "quantity": 3}],
        }
        CountSnapshot.model_validate(value)
        body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO employee_referral_paid_outbox(order_id,event_id,body,body_sha256,created_at) VALUES(100,%s,%s,%s,%s)",
                (paid_id, body, hashlib.sha256(body).hexdigest(), now),
            )
        original = paid_fingerprint()
        for operation, target in [
            ("upgrade", "b5b2e7f9a341"),
            ("downgrade", "b5a1d6e8f230"),
            ("upgrade", "b5b2e7f9a341"),
        ]:
            alembic(operation, target)
            assert paid_fingerprint() == original
        # Current metadata includes G6a; validate it, then return to this historical transition.
        alembic("upgrade", "head")
        alembic("check")
        with connection.cursor() as cur:
            cur.execute("TRUNCATE employee_referral_k2_delivery, employee_referral_notice_delivery")
        alembic("downgrade", "b5b2e7f9a341")
        with connection.cursor() as cur:
            cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            cur.execute(
                sql.SQL(
                    "GRANT SELECT,INSERT,UPDATE,DELETE ON employee_referral_paid_outbox,employee_referral_return_outbox TO {}"
                ).format(sql.Identifier(role))
            )
            cur.execute(
                sql.SQL(
                    "GRANT USAGE ON SEQUENCE employee_referral_paid_outbox_id_seq TO {}"
                ).format(sql.Identifier(role))
            )
            cur.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            cur.execute(
                "SELECT order_id FROM employee_referral_paid_outbox WHERE order_id=100 FOR UPDATE"
            )
            assert cur.fetchone() == (100,)
            cur.execute("SELECT nextval('employee_referral_paid_outbox_id_seq')")
            position = cur.fetchone()[0]
            assert position == 2
            event_id = str(uuid4())
            value.update(
                event_id=event_id,
                event_type="sale_refunded",
                order_version=2,
                source_position=position,
            )
            value["lines"][0]["returned_quantity"] = 1
            CountSnapshot.model_validate(value)
            returned = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            cur.execute(
                "INSERT INTO employee_referral_return_outbox(id,return_id,order_id,order_version,confirmed_by,source,request_lines,body,body_sha256,created_at) VALUES(%s,%s,100,2,2,'shop-admin-confirmation',%s,%s,%s,%s)",
                (
                    position,
                    event_id,
                    json.dumps([{"order_item_id": "4201", "quantity": "1"}]),
                    returned,
                    hashlib.sha256(returned).hexdigest(),
                    now,
                ),
            )
            for statement in (
                "UPDATE employee_referral_return_outbox SET body=body",
                "DELETE FROM employee_referral_return_outbox",
            ):
                with pytest.raises(psycopg2.errors.CheckViolation):
                    cur.execute(statement)
            cur.execute("RESET ROLE")
        assert paid_fingerprint() == original
        assert "Referral returned history prevents downgrade" in alembic(
            "downgrade", "b5a1d6e8f230", ok=False
        )
        with connection.cursor() as cur:
            cur.execute("SELECT body FROM employee_referral_return_outbox")
            assert bytes(cur.fetchone()[0]) == returned
            cur.execute("SELECT version_num FROM alembic_version")
            assert cur.fetchone()[0] == "b5b2e7f9a341"
            cur.execute("SELECT pg_get_serial_sequence('employee_referral_return_outbox','id')")
            assert cur.fetchone()[0] is None
            cur.execute("SELECT pg_get_serial_sequence('employee_referral_paid_outbox','id')")
            assert cur.fetchone()[0] == "public.employee_referral_paid_outbox_id_seq"
            cur.execute("TRUNCATE employee_referral_return_outbox")
        alembic("downgrade", "b5a1d6e8f230")
        assert paid_fingerprint() == original
        alembic("upgrade", "head")
        with connection.cursor() as cur:
            cur.execute("SELECT nextval('employee_referral_paid_outbox_id_seq')")
            assert cur.fetchone()[0] == 3
        # Only the owned scratch superuser clears operational fixtures for the empty roundtrip.
        with connection.cursor() as cur:
            cur.execute("TRUNCATE employee_referral_k2_delivery, employee_referral_notice_delivery")
        alembic("downgrade", "base")
        alembic("upgrade", "head")
        alembic("check")
    finally:
        connection.close()
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            cur.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        admin.close()
