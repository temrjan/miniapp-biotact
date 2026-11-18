"""S1: real Alembic transactions on an explicitly disposable local PostgreSQL server.

Set S1_TEST_DATABASE_URL to the local test server's postgres database. Each test
owns a unique database; none uses conftest's metadata create/drop fixtures.
"""

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import jwt
import psycopg2
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from httpx import ASGITransport, AsyncClient
from psycopg2 import sql
from psycopg2.extensions import connection, parse_dsn
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src import database
from src.config import settings
from src.main import app

PREDECESSOR = "a8f6d2c93017"
REVISION = "b7e31c904a62"
API_ROOT = Path(__file__).resolve().parents[1]

# This launches the unchanged Alembic environment. The listener only observes
# settings, including at version advancement after the revision has returned.
ALEMBIC_RUNNER = """
import sys
from sqlalchemy import event
from sqlalchemy.engine import Engine
from alembic import command
from alembic.config import Config

@event.listens_for(Engine, 'before_cursor_execute')
def observe(conn, cursor, statement, parameters, context, executemany):
    if statement.startswith('LOCK TABLE ONLY public.categories'):
        label = 'S1_LIMITS'
    elif statement.startswith('UPDATE alembic_version') and 'b7e31c904a62' in statement:
        label = 'S1_RESTORED'
    else:
        return
    values = conn.exec_driver_sql(
        "SELECT current_setting('lock_timeout'), current_setting('statement_timeout')"
    ).one()
    print(label, *values, flush=True)

cfg = Config(sys.argv[1] + '/alembic.ini')
cfg.set_main_option('script_location', sys.argv[1] + '/alembic')
cfg.set_main_option('prepend_sys_path', sys.argv[1])
getattr(command, sys.argv[2])(cfg, sys.argv[3], sql='--sql' in sys.argv[4:])
"""


class ScratchDatabase:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = True
        values = parse_dsn(dsn)
        self.url = (
            f"postgresql+asyncpg://{values['user']}:{values['password']}"
            f"@{values['host']}:{values['port']}/{values['dbname']}"
        )

    def execute(self, statement: str, args: tuple | None = None) -> list:
        with self.conn.cursor() as cur:
            cur.execute(statement, args)
            return cur.fetchall() if cur.description else []

    def migrate(
        self,
        action: str = "upgrade",
        target: str = REVISION,
        *,
        success: bool = True,
        offline: bool = False,
    ) -> str:
        env = dict(os.environ, DATABASE_URL=self.url)
        result = subprocess.run(  # noqa: S603 - fixed interpreter and Alembic runner
            [sys.executable, "-c", ALEMBIC_RUNNER, str(API_ROOT), action, target]
            + (["--sql"] if offline else []),
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=100,
        )
        output = result.stdout + result.stderr
        assert (result.returncode == 0) == success, output
        return output

    def drift(self, default: str = "false") -> None:
        self.execute("ALTER TABLE categories ALTER COLUMN is_purchasable DROP NOT NULL")
        if default == "absent":
            self.execute("ALTER TABLE categories ALTER COLUMN is_purchasable DROP DEFAULT")
        else:
            assert default in {"true", "false"}
            self.execute(
                f"ALTER TABLE categories ALTER COLUMN is_purchasable SET DEFAULT {default}"
            )

    def seed(self) -> None:
        self.execute("""
            INSERT INTO categories (id,name,name_uz,slug,sort_order,is_active,is_purchasable)
            VALUES (701,'Synthetic true','UZ true','s1-true',3,true,true),
                   (702,'Synthetic false','UZ false','s1-false',1,true,false);
            INSERT INTO users (id,telegram_id,language_code,is_active,is_admin)
            VALUES (701,990701,'ru',true,true);
            INSERT INTO products (id,name,slug,price,currency,stock_quantity,is_in_stock,
                                  sort_order,is_active,is_featured,category_id)
            VALUES (701,'Synthetic product','s1-product',100,'UZS',9,true,2,true,false,702);
            INSERT INTO orders (id,order_number,user_id,status,subtotal,delivery_fee,total,currency)
            VALUES (701,'S1-synthetic',701,'PENDING',100,0,100,'UZS');
            INSERT INTO order_items (id,order_id,product_id,product_name,product_price,quantity,total)
            VALUES (701,701,701,'Synthetic product',100,1,100);
            INSERT INTO referrers (id,code,employee_name,telegram_id)
            VALUES (1,'synthetic','Synthetic employee',990701);
            INSERT INTO referral_invites (id,invite_code,used_by_referrer_id)
            VALUES (1,'s1-invite',1);
            UPDATE users SET referral_code='synthetic' WHERE id=701;
            UPDATE orders SET referral_code='synthetic' WHERE id=701;
            CREATE FUNCTION s1_detect_dml() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'S1 must not rewrite categories'; END $$;
            CREATE TRIGGER s1_no_dml BEFORE UPDATE OR DELETE ON categories
            FOR EACH ROW EXECUTE FUNCTION s1_detect_dml();
        """)

    def snapshot(self) -> tuple:
        """Observe all public rows and native schema, excluding only the two allowed changes."""
        tables = self.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        )
        data = []
        with self.conn.cursor() as cur:
            for (table,) in tables:
                if table == "alembic_version":
                    continue
                cur.execute(
                    sql.SQL("SELECT row_to_json(t)::text FROM {} t ORDER BY 1").format(
                        sql.Identifier(table)
                    )
                )
                data.append((table, cur.fetchall()))
        schema = self.execute("""
            SELECT c.relname,a.attnum,a.attname,a.atttypid,a.atttypmod,
              CASE WHEN c.relname='categories' AND a.attname='is_purchasable'
                   THEN NULL ELSE a.attnotnull END,
              CASE WHEN c.relname='categories' AND a.attname='is_purchasable'
                   THEN NULL ELSE pg_get_expr(d.adbin,d.adrelid) END,
              a.attidentity,a.attgenerated,a.attacl,c.relacl,c.relkind,c.relpersistence
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
            LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE n.nspname='public' ORDER BY c.relname,a.attnum
        """)
        indexes = self.execute(
            "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY 1"
        )
        constraints = self.execute("""
            SELECT c.conname,pg_get_constraintdef(c.oid) FROM pg_constraint c
            JOIN pg_namespace n ON n.oid=c.connamespace
            WHERE n.nspname='public' ORDER BY 1,2
        """)
        triggers = self.execute("""
            SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t
            JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname='public' ORDER BY 1
        """)
        return data, schema, indexes, constraints, triggers

    def shape(self) -> list:
        return self.execute("""
            SELECT a.atttypid='pg_catalog.bool'::regtype,a.attnotnull,
                   pg_get_expr(d.adbin,d.adrelid)
            FROM pg_attribute a LEFT JOIN pg_attrdef d
              ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid='public.categories'::regclass
              AND a.attname='is_purchasable' AND NOT a.attisdropped
        """)

    def assert_native(self) -> None:
        assert self.shape() == [(True, True, "true")], (
            "native boolean NOT NULL DEFAULT true required"
        )

    def marker(self) -> str:
        return self.execute("SELECT version_num FROM alembic_version")[0][0]

    def wait_for(self, predicate, description: str, timeout: float = 12) -> object:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            # Bounded polling of observed server state, never a race-ordering sleep.
            time.sleep(0.02)
        pytest.fail(f"Did not observe {description}")

    def waiting_migration(self) -> list:
        return self.execute("""
            SELECT a.pid FROM pg_stat_activity a
            WHERE a.datname=current_database() AND a.pid<>pg_backend_pid()
              AND a.wait_event_type='Lock' AND a.query LIKE 'LOCK TABLE ONLY public.categories%'
        """)


@pytest.fixture
def scratch() -> Iterator[ScratchDatabase]:
    url = os.environ.get("S1_TEST_DATABASE_URL", "")
    if not url:
        pytest.fail(
            "S1_TEST_DATABASE_URL must explicitly identify the disposable local PostgreSQL server"
        )
    params = parse_dsn(url)
    if (
        params.get("host") not in {"127.0.0.1", "localhost", "::1"}
        or params.get("dbname") != "postgres"
    ):
        pytest.fail(
            "S1_TEST_DATABASE_URL must target a local disposable server's postgres database"
        )
    name = "s1_migration_" + uuid4().hex
    admin = psycopg2.connect(url)
    admin.autocommit = True
    db = None
    try:
        with admin.cursor() as cur:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        db = ScratchDatabase(psycopg2.extensions.make_dsn(url, dbname=name))
        yield db
    finally:
        if db is not None:
            db.conn.close()
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        admin.close()


@contextmanager
def blocker(db: ScratchDatabase) -> Iterator[connection]:
    conn = psycopg2.connect(db.dsn)
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def test_clean_chain_native_contract_and_offline_refusal(scratch: ScratchDatabase) -> None:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert len(script.get_heads()) == 1
    assert REVISION in {revision.revision for revision in script.walk_revisions()}
    assert script.get_revision(REVISION).down_revision == PREDECESSOR
    scratch.migrate()
    scratch.assert_native()
    scratch.execute(
        "INSERT INTO categories (name,slug,sort_order,is_active) VALUES ('Omitted','omitted',0,true)"
    )
    scratch.execute(
        "INSERT INTO categories (name,slug,sort_order,is_active,is_purchasable) VALUES ('False','false',0,true,false)"
    )
    assert scratch.execute("SELECT slug,is_purchasable FROM categories ORDER BY slug") == [
        ("false", False),
        ("omitted", True),
    ]
    with pytest.raises(psycopg2.errors.NotNullViolation):
        scratch.execute(
            "INSERT INTO categories (name,slug,sort_order,is_active,is_purchasable) VALUES ('NULL','null',0,true,NULL)"
        )
    with pytest.raises(psycopg2.errors.NotNullViolation):
        scratch.execute("UPDATE categories SET is_purchasable=NULL WHERE slug='false'")
    output = scratch.migrate(target=PREDECESSOR + ":" + REVISION, success=False, offline=True)
    assert "requires an online PostgreSQL transaction" in output


@pytest.mark.parametrize("default", ["true", "absent", "false"])
def test_existing_nullable_regression_and_exact_preservation(
    scratch: ScratchDatabase, default: str
) -> None:
    scratch.migrate(target=PREDECESSOR)
    scratch.drift(default)
    scratch.seed()
    before = scratch.snapshot()
    # Same independent assertion fails behaviorally on the unchanged predecessor.
    with pytest.raises(AssertionError, match="native boolean NOT NULL"):
        scratch.assert_native()
    scratch.migrate()
    scratch.assert_native()
    assert scratch.snapshot() == before
    assert scratch.marker() == REVISION
    for action, target in (
        ("upgrade", REVISION),
        ("downgrade", PREDECESSOR),
        ("upgrade", REVISION),
    ):
        scratch.migrate(action, target)
        scratch.assert_native()
        assert scratch.snapshot() == before
    scratch.execute("ALTER TABLE categories ALTER COLUMN is_purchasable DROP NOT NULL")
    scratch.migrate()  # The head marker skips S1; it is not native schema evidence.
    assert scratch.marker() == REVISION
    with pytest.raises(AssertionError, match="native boolean NOT NULL"):
        scratch.assert_native()


def test_null_refusal_keeps_default_data_and_marker(scratch: ScratchDatabase) -> None:
    scratch.migrate(target=PREDECESSOR)
    scratch.drift()
    scratch.seed()
    scratch.execute(
        "INSERT INTO categories (id,name,slug,sort_order,is_active,is_purchasable) VALUES (703,'NULL','null',7,true,NULL)"
    )
    before, shape = scratch.snapshot(), scratch.shape()
    output = scratch.migrate(success=False)
    assert "S1 NULL preflight" in output
    assert scratch.snapshot() == before
    assert scratch.shape() == shape
    assert scratch.marker() == PREDECESSOR


@pytest.mark.parametrize(
    "change",
    [
        "ALTER TABLE categories DROP COLUMN is_purchasable",
        "ALTER TABLE categories ALTER COLUMN is_purchasable DROP DEFAULT; ALTER TABLE categories ALTER COLUMN is_purchasable TYPE text USING is_purchasable::text",
        "ALTER TABLE categories RENAME TO saved_categories",
        "CREATE TABLE s1_child () INHERITS (categories)",
        "ALTER TABLE categories ENABLE ROW LEVEL SECURITY",
    ],
)
def test_unexpected_schema_stops_without_changes(scratch: ScratchDatabase, change: str) -> None:
    scratch.migrate(target=PREDECESSOR)
    scratch.seed()
    scratch.execute(change)
    before = scratch.snapshot()
    assert "S1 schema preflight" in scratch.migrate(success=False)
    assert scratch.snapshot() == before
    assert scratch.marker() == PREDECESSOR


def test_unsupported_isolation_stops(scratch: ScratchDatabase) -> None:
    scratch.migrate(target=PREDECESSOR)
    scratch.execute(
        sql.SQL("ALTER DATABASE {} SET default_transaction_isolation='repeatable read'").format(
            sql.Identifier(parse_dsn(scratch.dsn)["dbname"])
        )
    )
    before, shape = scratch.snapshot(), scratch.shape()
    assert "requires READ COMMITTED" in scratch.migrate(success=False)
    assert scratch.snapshot() == before
    assert scratch.shape() == shape
    assert scratch.marker() == PREDECESSOR


def test_lock_timeout_releases_locks_and_preserves_state(scratch: ScratchDatabase) -> None:
    scratch.migrate(target=PREDECESSOR)
    scratch.drift()
    before, shape = scratch.snapshot(), scratch.shape()
    with blocker(scratch) as writer, ThreadPoolExecutor(max_workers=1) as pool:
        with writer.cursor() as cur:
            cur.execute("LOCK TABLE categories IN ACCESS EXCLUSIVE MODE")
        future = pool.submit(scratch.migrate, success=False)
        scratch.wait_for(scratch.waiting_migration, "migration waiting on category lock")
        output = future.result(timeout=15)
        assert "lock timeout" in output
        assert "S1_LIMITS 5s 30s" in output
    assert not scratch.waiting_migration()
    with blocker(scratch) as probe, probe.cursor() as cur:
        cur.execute("LOCK TABLE categories IN ACCESS EXCLUSIVE MODE NOWAIT")
    assert scratch.snapshot() == before
    assert scratch.shape() == shape
    assert scratch.marker() == PREDECESSOR


def test_null_committed_before_lock_is_seen(scratch: ScratchDatabase) -> None:
    scratch.migrate(target=PREDECESSOR)
    scratch.drift()
    with blocker(scratch) as writer, ThreadPoolExecutor(max_workers=1) as pool:
        with writer.cursor() as cur:
            cur.execute(
                "INSERT INTO categories (name,slug,sort_order,is_active,is_purchasable) VALUES ('Race','race',0,true,NULL)"
            )
        future = pool.submit(scratch.migrate, success=False)
        scratch.wait_for(scratch.waiting_migration, "migration behind uncommitted NULL writer")
        writer.commit()
        output = future.result(timeout=15)
    assert "S1 NULL preflight" in output
    assert scratch.execute("SELECT is_purchasable FROM categories WHERE slug='race'") == [(None,)]
    assert scratch.shape() == [(True, False, "false")]
    assert scratch.marker() == PREDECESSOR


def install_ddl_barrier(db: ScratchDatabase) -> None:
    # Runs after real DDL inside the actual migration transaction. The extra
    # event trigger is test-owned, never part of the product migration.
    db.execute("""
        CREATE FUNCTION s1_ddl_barrier() RETURNS event_trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF current_query() LIKE 'ALTER TABLE ONLY public.categories%' THEN
            PERFORM pg_advisory_xact_lock(73109);
          END IF;
        END $$;
        CREATE EVENT TRIGGER s1_barrier ON ddl_command_end EXECUTE FUNCTION s1_ddl_barrier();
    """)


def waiting_ddl(db: ScratchDatabase) -> list:
    return db.execute("""
        SELECT pid FROM pg_stat_activity WHERE datname=current_database()
        AND wait_event='advisory' AND query LIKE 'ALTER TABLE ONLY public.categories%'
    """)


def test_null_writer_after_migration_lock_cannot_persist(scratch: ScratchDatabase) -> None:
    scratch.migrate(target=PREDECESSOR)
    scratch.drift()
    install_ddl_barrier(scratch)
    with (
        blocker(scratch) as gate,
        blocker(scratch) as writer,
        ThreadPoolExecutor(max_workers=2) as pool,
    ):
        with gate.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(73109)")
        migration = pool.submit(scratch.migrate)
        scratch.wait_for(lambda: waiting_ddl(scratch), "migration holding table lock after DDL")

        def write_null() -> str:
            with writer.cursor() as cur:
                with pytest.raises(psycopg2.errors.NotNullViolation) as exc:
                    cur.execute(
                        "INSERT INTO categories (name,slug,sort_order,is_active,is_purchasable) VALUES ('Late','late',0,true,NULL)"
                    )
                return exc.value.pgcode

        write = pool.submit(write_null)
        scratch.wait_for(
            lambda: scratch.execute(
                "SELECT 1 FROM pg_stat_activity WHERE pid=%s AND wait_event_type='Lock'",
                (writer.get_backend_pid(),),
            ),
            "late writer blocked on migration",
        )
        gate.commit()
        migration.result(timeout=15)
        assert write.result(timeout=15) == "23502"
    scratch.assert_native()
    assert scratch.execute("SELECT count(*) FROM categories WHERE slug='late'") == [(0,)]


@pytest.mark.parametrize("failure", ["ddl", "cancel", "statement_timeout"])
def test_failure_after_ddl_rolls_back_schema_and_marker(
    scratch: ScratchDatabase, failure: str
) -> None:
    scratch.migrate(target=PREDECESSOR)
    scratch.drift()
    scratch.seed()
    if failure == "ddl":
        scratch.execute("""
            CREATE FUNCTION s1_fail_ddl() RETURNS event_trigger LANGUAGE plpgsql AS $$
            BEGIN IF current_query() LIKE 'ALTER TABLE ONLY public.categories%'
              THEN RAISE EXCEPTION 'synthetic failure after DDL'; END IF; END $$;
            CREATE EVENT TRIGGER s1_fail ON ddl_command_end EXECUTE FUNCTION s1_fail_ddl();
        """)
    else:
        scratch.execute("""
            CREATE FUNCTION s1_slow_ddl() RETURNS event_trigger LANGUAGE plpgsql AS $$
            BEGIN IF current_query() LIKE 'ALTER TABLE ONLY public.categories%'
              THEN PERFORM pg_sleep(31); END IF; END $$;
            CREATE EVENT TRIGGER s1_slow ON ddl_command_end EXECUTE FUNCTION s1_slow_ddl();
        """)
    before, shape = scratch.snapshot(), scratch.shape()
    with blocker(scratch) as gate, ThreadPoolExecutor(max_workers=1) as pool:
        with gate.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(73109)")
        future = pool.submit(scratch.migrate, success=False)
        if failure != "ddl":
            pid = scratch.wait_for(
                lambda: scratch.execute(
                    "SELECT pid FROM pg_stat_activity WHERE datname=current_database() "
                    "AND wait_event='PgSleep' AND query LIKE 'ALTER TABLE ONLY public.categories%'"
                ),
                "DDL blocked after schema change",
            )[0][0]
            if failure == "cancel":
                assert scratch.execute("SELECT pg_cancel_backend(%s)", (pid,)) == [(True,)]
        output = future.result(timeout=40)
    expected = {
        "ddl": "synthetic failure after DDL",
        "cancel": "canceling statement due to user request",
        "statement_timeout": "statement timeout",
    }
    assert expected[failure] in output
    assert scratch.snapshot() == before
    assert scratch.shape() == shape
    assert scratch.marker() == PREDECESSOR
    with blocker(scratch) as probe, probe.cursor() as cur:
        cur.execute("LOCK TABLE categories IN ACCESS EXCLUSIVE MODE NOWAIT")


def test_timeout_settings_restored_on_same_connection(scratch: ScratchDatabase) -> None:
    scratch.migrate(target=PREDECESSOR)
    name = parse_dsn(scratch.dsn)["dbname"]
    scratch.execute(
        sql.SQL("ALTER DATABASE {} SET lock_timeout='11s'").format(sql.Identifier(name))
    )
    scratch.execute(
        sql.SQL("ALTER DATABASE {} SET statement_timeout='41s'").format(sql.Identifier(name))
    )
    output = scratch.migrate()
    assert "S1_LIMITS 5s 30s" in output
    assert "S1_RESTORED 11s 41s" in output


@pytest.mark.asyncio
async def test_api_compatibility_on_migrated_schema(scratch: ScratchDatabase, monkeypatch) -> None:
    await asyncio.to_thread(scratch.migrate)
    scratch.seed()
    scratch.execute("DROP TRIGGER s1_no_dml ON categories")
    engine = create_async_engine(scratch.url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    # Exercise the real get_db commit/rollback implementation, not a copy.
    monkeypatch.setattr(database, "async_session_maker", maker)
    token = jwt.encode(
        {"sub": "701", "type": "admin", "exp": int(time.time()) + 3600},
        settings.secret_key.get_secret_value(),
        algorithm=settings.algorithm,
    )
    headers = {"Authorization": "Bearer " + token}
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://s1.test"
        ) as client:
            assert (
                await client.post("/api/v1/categories", json={"name": "Denied", "slug": "denied"})
            ).status_code in {401, 403}
            for slug, fields, expected in (
                ("omitted", {}, True),
                ("explicit-false", {"is_purchasable": False}, False),
                ("explicit-true", {"is_purchasable": True}, True),
            ):
                response = await client.post(
                    "/api/v1/categories",
                    json={"name": slug, "slug": slug, **fields},
                    headers=headers,
                )
                assert response.status_code == 201, response.text
                assert response.json()["is_purchasable"] is expected
            response = await client.patch(
                "/api/v1/categories/702", json={"name": "Changed"}, headers=headers
            )
            assert response.status_code == 200
            assert response.json()["is_purchasable"] is False
            for flag in (True, False):
                response = await client.patch(
                    "/api/v1/categories/702", json={"is_purchasable": flag}, headers=headers
                )
                assert response.status_code == 200
                assert response.json()["is_purchasable"] is flag
            response = await client.patch(
                "/api/v1/categories/702",
                json={"name": "Must roll back", "is_purchasable": None},
                headers=headers,
            )
            assert response.status_code == 409
            response = await client.get("/api/v1/categories/702")
            assert response.status_code == 200
            assert response.json()["is_purchasable"] is False
            assert response.json()["name"] == "Changed"
            for path in ("/api/v1/products/701", "/api/v1/products/slug/s1-product"):
                response = await client.get(path)
                assert response.status_code == 200
                assert response.json()["is_purchasable"] is False
            for path, slug in (
                ("/api/v1/categories", "s1-false"),
                ("/api/v1/products", "s1-product"),
            ):
                response = await client.get(path)
                assert response.status_code == 200
                assert (
                    next(item for item in response.json()["items"] if item["slug"] == slug)[
                        "is_purchasable"
                    ]
                    is False
                )
        scratch.assert_native()
        assert scratch.execute("SELECT name,is_purchasable FROM categories WHERE id=702") == [
            ("Changed", False)
        ]
    finally:
        await engine.dispose()
