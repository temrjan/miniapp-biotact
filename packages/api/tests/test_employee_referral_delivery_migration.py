"""Exact G5b populated transition, app-role immutability and empty parity roundtrip."""

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


def test_additive_legacy_cutover_and_immutable_operational_role():
    base = settings.database_url.rsplit("/", 1)[0].replace("+asyncpg", "")
    name, role = "g6a_migration_" + uuid4().hex, "g6a_role_" + uuid4().hex
    admin = psycopg2.connect(base + "/postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        cur.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER").format(sql.Identifier(role)))
    connection = psycopg2.connect(base + "/" + name)
    connection.autocommit = True

    def migrate(*args, ok=True):
        result = subprocess.run(  # noqa: S603 - fixed interpreter/Alembic on own scratch DB
            [sys.executable, "-m", "alembic", *args],
            env=dict(
                os.environ,
                DATABASE_URL=base.replace("postgresql://", "postgresql+asyncpg://") + "/" + name,
            ),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert (result.returncode == 0) is ok, result.stdout + result.stderr
        return result.stdout + result.stderr

    def fingerprint():
        with connection.cursor() as cur:
            result = []
            for table in ("employee_referral_paid_outbox", "employee_referral_return_outbox"):
                cur.execute(
                    sql.SQL("SELECT row_to_json(t)::text FROM {} t ORDER BY id").format(
                        sql.Identifier(table)
                    )
                )
                result.extend(cur.fetchall())
            cur.execute("SELECT last_value FROM employee_referral_paid_outbox_id_seq")
            result.extend(cur.fetchall())
            return result

    try:
        migrate("upgrade", "head")
        migrate("check")
        migrate("downgrade", "base")
        migrate("upgrade", "b5b2e7f9a341")
        stamp = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        value = {
            "schema_version": 1,
            "event_id": str(uuid4()),
            "event_type": "sale_paid",
            "source": "biotact-shop",
            "order_id": 100,
            "order_version": 1,
            "source_position": 1,
            "occurred_at": stamp,
            "source_recorded_at": stamp,
            "attribution": {
                "campaign_id": 1,
                "employee_code": "22222222-2222-4222-8222-222222222222",
                "product_id": 39,
                "content_revision": 1,
                "captured_at": stamp,
            },
            "lines": [{"order_item_id": 4201, "product_id": 39, "quantity": 3}],
        }
        CountSnapshot.model_validate(value)
        body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO employee_referral_paid_outbox(order_id,event_id,body,body_sha256,created_at) VALUES(100,%s,%s,%s,%s)",
                (value["event_id"], body, hashlib.sha256(body).hexdigest(), stamp),
            )
            cur.execute("SELECT nextval('employee_referral_paid_outbox_id_seq')")
            position = cur.fetchone()[0]
            value.update(
                event_id=str(uuid4()),
                event_type="sale_refunded",
                order_version=2,
                source_position=position,
            )
            value["lines"][0]["returned_quantity"] = 1
            CountSnapshot.model_validate(value)
            body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            cur.execute(
                "INSERT INTO employee_referral_return_outbox(id,return_id,order_id,order_version,confirmed_by,source,request_lines,body,body_sha256,created_at) VALUES(%s,%s,100,2,2,'shop-admin-confirmation',%s,%s,%s,%s)",
                (
                    position,
                    value["event_id"],
                    json.dumps([{"order_item_id": "4201", "quantity": "1"}]),
                    body,
                    hashlib.sha256(body).hexdigest(),
                    stamp,
                ),
            )
        before = fingerprint()
        migrate("upgrade", "head")
        migrate("check")
        assert fingerprint() == before
        with connection.cursor() as cur:
            cur.execute(
                "SELECT state,attempt_count,ack FROM employee_referral_k2_delivery ORDER BY id"
            )
            assert cur.fetchall() == [("pending", 0, None), ("pending", 0, None)]
            cur.execute(
                "SELECT state,payload,payload_sha256,message_id,completed_at FROM employee_referral_notice_delivery ORDER BY id"
            )
            assert cur.fetchall() == [("legacy_unknown", None, None, None, None)] * 2
            cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            cur.execute(
                sql.SQL(
                    "GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO {}"
                ).format(sql.Identifier(role))
            )
            cur.execute(
                sql.SQL("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}").format(
                    sql.Identifier(role)
                )
            )
            cur.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            cur.execute(
                "UPDATE employee_referral_k2_delivery SET generation=generation+1,next_attempt_at=clock_timestamp()"
            )
            for statement in (
                "UPDATE employee_referral_k2_delivery SET source_kind='return'",
                "UPDATE employee_referral_k2_delivery SET paid_id=NULL",
                "DELETE FROM employee_referral_k2_delivery",
                "DELETE FROM employee_referral_notice_delivery",
                "UPDATE employee_referral_notice_delivery SET state='pending'",
                "UPDATE employee_referral_notice_delivery SET payload='bad'::bytea",
            ):
                with pytest.raises(psycopg2.errors.CheckViolation):
                    cur.execute(statement)
            cur.execute("RESET ROLE")
        assert "Referral delivery history prevents downgrade" in migrate(
            "downgrade", "b5b2e7f9a341", ok=False
        )
        assert fingerprint() == before
        with connection.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            assert cur.fetchone()[0] == "b6a3c8d1e240"
            cur.execute("TRUNCATE employee_referral_k2_delivery,employee_referral_notice_delivery")
        migrate("downgrade", "b5b2e7f9a341")
        assert fingerprint() == before
        migrate("upgrade", "head")
        assert fingerprint() == before
    finally:
        connection.close()
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
            cur.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
        admin.close()
