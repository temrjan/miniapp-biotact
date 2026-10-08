"""T6: populated predecessor, native constraints and guarded downgrade on owned PG."""

import os
import subprocess
import sys
from uuid import uuid4

import psycopg2
import pytest
from psycopg2 import sql

from src.auth.passwords import DUMMY_HASH
from src.config import settings


def test_account_migration_native_constraints_and_guarded_roundtrip():
    base = settings.database_url.rsplit("/", 1)[0]
    name = "admin_accounts_migration_" + uuid4().hex
    admin = psycopg2.connect(base.replace("+asyncpg", "") + "/postgres")
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = base + "/" + name
    env = dict(os.environ, DATABASE_URL=url)

    def alembic(*args, success=True):
        result = subprocess.run(  # noqa: S603 — fixed test-owned Alembic command
            [sys.executable, "-m", "alembic", *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        return result.stdout + result.stderr

    try:
        alembic("upgrade", "b6a3c8d1e240")
        conn = psycopg2.connect(url.replace("+asyncpg", ""))
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (id,telegram_id,language_code,is_active,is_admin) VALUES (901,99901,'ru',true,false),(902,99902,'ru',true,false)"
                )
                cur.execute(
                    "INSERT INTO admin_users (id,email,password_hash,is_verified,is_active) VALUES (901,'synthetic@example.invalid','legacy-marker',false,true)"
                )

            def existing():
                with conn.cursor() as cur:
                    result = []
                    for table in ("users", "admin_users"):
                        cur.execute(
                            sql.SQL("SELECT row_to_json(t)::text FROM {} t ORDER BY id").format(
                                sql.Identifier(table)
                            )
                        )
                        result.append(cur.fetchall())
                    return result

            before = existing()
            alembic("upgrade", "head")
            alembic("check")
            assert existing() == before
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM admin_password_accounts")
                assert cur.fetchone()[0] == 0
                cur.execute(
                    "INSERT INTO admin_password_accounts VALUES (901,%s,%s)",
                    ("fictional", DUMMY_HASH),
                )
                cur.execute("SELECT row_to_json(t)::text FROM admin_password_accounts t")
                stored = cur.fetchall()
                for user_id, login, hashed in [
                    (902, "fictional", DUMMY_HASH),
                    (901, "another", DUMMY_HASH),
                    (999, "missing", DUMMY_HASH),
                    (902, "biotact", DUMMY_HASH),
                    (902, "Upper", DUMMY_HASH),
                    (902, "proper", "wrong-hash"),
                    (902, "proper", DUMMY_HASH.replace("$12$", "$13$")),
                ]:
                    with pytest.raises(psycopg2.IntegrityError):
                        cur.execute(
                            "INSERT INTO admin_password_accounts VALUES (%s,%s,%s)",
                            (user_id, login, hashed),
                        )
            output = alembic("downgrade", "b6a3c8d1e240", success=False)
            assert "admin_password_accounts is not empty; downgrade refused" in output
            with conn.cursor() as cur:
                cur.execute("SELECT version_num FROM alembic_version")
                assert cur.fetchone()[0] == "c4f8a921d730"
                cur.execute("SELECT row_to_json(t)::text FROM admin_password_accounts t")
                assert cur.fetchall() == stored
                cur.execute("DELETE FROM users WHERE id=901")
                cur.execute("SELECT count(*) FROM admin_password_accounts")
                assert cur.fetchone()[0] == 0
            retained = existing()
            alembic("downgrade", "b6a3c8d1e240")
            assert existing() == retained
            alembic("upgrade", "head")
            alembic("check")
            assert existing() == retained
        finally:
            conn.close()
    finally:
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        admin.close()
