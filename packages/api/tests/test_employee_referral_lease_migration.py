"""Actual PostgreSQL upgrade, historical preservation and atomic refusal."""

import os
import subprocess
import sys
from uuid import uuid4

import psycopg2
import pytest
from psycopg2 import sql

from src.config import settings


def test_lease_expansion_and_refused_downgrade_preserve_revision_and_data():
    base = settings.database_url.rsplit("/", 1)[0]
    name = "ttl_migration_" + uuid4().hex
    admin = psycopg2.connect(base.replace("+asyncpg", "") + "/postgres")
    admin.autocommit = True
    with admin.cursor() as cursor:
        # The connected test role owns its scratch database in local and CI runs.
        cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = base + "/" + name
    env = dict(os.environ, DATABASE_URL=url)

    def alembic(*args, succeeds=True):
        result = subprocess.run(  # noqa: S603 - fixed owned interpreter and Alembic
            [sys.executable, "-m", "alembic", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert (result.returncode == 0) is succeeds, result.stdout + result.stderr
        return result.stdout + result.stderr

    alembic("upgrade", "b6d4e8f2a301")
    connection = psycopg2.connect(url.replace("+asyncpg", ""))
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute("""INSERT INTO employee_referral_campaigns
          (campaign_id,content_revision,content_sha256,name,product_id,state,
           published_at,start_at,end_at,ended_at,issued_at,valid_until,observed_at)
          VALUES (1,1,repeat('0',64),'Historical ended',39,'ended',
            '2026-01-01 00:00Z','2026-01-01 00:00Z','2026-02-01 00:00Z','2026-01-02 00:00Z',
            '2026-01-02 00:00Z','2026-01-02 00:01Z','2026-01-02 00:00Z')""")

    def snapshot():
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM employee_referral_campaigns ORDER BY campaign_id")
            rows = cursor.fetchall()
            cursor.execute("SELECT version_num FROM alembic_version")
            revision = cursor.fetchone()[0]
            cursor.execute("""SELECT pg_get_constraintdef(oid) FROM pg_constraint
                 WHERE conname='ck_employee_referral_campaigns_lease'""")
            return rows, revision, cursor.fetchone()[0]

    old_rows = snapshot()[0]
    alembic("upgrade", "c4f8a921d730")
    assert snapshot()[0] == old_rows
    assert snapshot()[1] == "c4f8a921d730"
    alembic("upgrade", "head")
    assert snapshot()[0] == old_rows
    assert "No new upgrade operations detected" in alembic("check")
    for seconds in (-60, 0, 59, 60.000001, 299, 300.000001, 301):
        with pytest.raises(psycopg2.errors.CheckViolation), connection.cursor() as cursor:
            cursor.execute(
                "UPDATE employee_referral_campaigns SET valid_until = "
                "issued_at + (%s * interval '1 second') WHERE campaign_id=1",
                (seconds,),
            )
        assert snapshot()[0] == old_rows
    with connection.cursor() as cursor:
        cursor.execute("""INSERT INTO employee_referral_campaigns
          (campaign_id,content_revision,content_sha256,name,product_id,state,
           published_at,start_at,end_at,ended_at,issued_at,valid_until,observed_at)
          VALUES (2,1,repeat('1',64),'Five minute ended',39,'ended',
            '2026-01-01 00:00Z','2026-01-01 00:00Z','2026-02-01 00:00Z','2026-01-02 00:00Z',
            '2026-01-02 00:00Z','2026-01-02 00:05Z','2026-01-02 00:00Z')""")
    before = snapshot()
    assert "downgrade refused" in alembic("downgrade", "b6d4e8f2a301", succeeds=False)
    assert snapshot() == before
    # This database and its rows are owned synthetic fixtures; no production data.
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM employee_referral_campaigns WHERE campaign_id=2")
    alembic("downgrade", "b6d4e8f2a301")
    assert snapshot()[0] == old_rows
    alembic("upgrade", "head")
    assert snapshot()[0] == old_rows
    assert "No new upgrade operations detected" in alembic("check")
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM employee_referral_campaigns")
    alembic("downgrade", "base")
    alembic("upgrade", "head")
    assert "No new upgrade operations detected" in alembic("check")
    connection.close()
    admin.close()
