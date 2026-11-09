"""G1 guest-order migration (spec S2 DB-01, DB-02; check CK-01) on real Alembic transactions.

Each test owns a scratch database on the disposable local PostgreSQL 16 named by
S1_TEST_DATABASE_URL (see ``test_category_schema_migration``).
"""

from concurrent.futures import ThreadPoolExecutor

import psycopg2
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg2 import sql

from tests import test_category_schema_migration as migration
from tests.test_buyer_auth_native import check_parity

scratch = migration.scratch
PREVIOUS = "8d1b1791159b"
REVISION = "b4f4c51eb14a"
# The event promotion revision follows G1; these tests target G1 itself, not the moving head.
SUCCESSOR = "87688cb444f4"
NEW_COLUMNS = (
    "guest_access_digest",
    "guest_idempotency_digest",
    "legal_terms_version",
    "legal_privacy_version",
    "legal_accepted_at",
)
GUEST_INSERT = (
    "INSERT INTO orders (id,order_number,user_id,status,subtotal,delivery_fee,total,currency,"
    "points_applied,guest_access_digest,guest_idempotency_digest,legal_terms_version,"
    "legal_privacy_version,legal_accepted_at) VALUES "
    "(%s,%s,NULL,'PENDING',100,0,100,'UZS',%s,%s,%s,%s,%s,%s)"
)
COMPLETE = ("a" * 64, "b" * 64, "terms-v1", "policy-v1", "2026-10-27T10:00:00+00:00")


def seed(db: migration.ScratchDatabase) -> None:
    """Owned orders before the revision: the shared unsized seed plus one live sized order."""
    db.seed()
    db.execute("""
        INSERT INTO products (id,name,slug,price,currency,stock_quantity,is_in_stock,
                              sort_order,is_active,is_featured,category_id,sizes_initialized_at)
        VALUES (702,'Synthetic sized','g1-sized',30000,'UZS',0,true,3,true,false,702,now());
        INSERT INTO product_sizes (id,product_id,size,available_quantity,revision)
        VALUES (702,702,'L',49,1);
        INSERT INTO orders (id,order_number,user_id,status,subtotal,delivery_fee,total,currency,
                            points_applied,payment_uuid,payment_status,checkout_url,
                            size_stock_protocol,size_owner_state,accepted_settlement,
                            owned_invoice_uuid,creation_token,size_revision)
        VALUES (702,'G1-sized-owned',701,'PENDING',30000,0,30000,'UZS',0,'inv-702','pending',
                'https://pay.example.test/inv-702','size_stock_v1','live',false,'inv-702',
                'token-702',2);
        INSERT INTO order_items (id,order_id,product_id,product_name,product_price,quantity,total,
                                 size,size_id)
        VALUES (702,702,702,'Synthetic sized',30000,1,30000,'L',702);
    """)


def values(db: migration.ScratchDatabase) -> list:
    """Every public row, with the five new order columns removed (absent before the revision)."""
    tables = db.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' "
        "AND tablename <> 'alembic_version' ORDER BY tablename"
    )
    rows = []
    for (table,) in tables:
        projected = sql.SQL(
            "(row_to_json(t)::jsonb - %(new)s::text[])::text"
            if table == "orders"
            else "row_to_json(t)::text"
        )
        statement = sql.SQL("SELECT {} FROM {} t ORDER BY 1").format(
            projected, sql.Identifier(table)
        )
        with db.conn.cursor() as cur:
            cur.execute(statement, {"new": list(NEW_COLUMNS)})
            rows.append((table, cur.fetchall()))
    return rows


def definitions(db: migration.ScratchDatabase) -> tuple[list, list]:
    constraints = db.execute("""
        SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid='public.orders'::regclass ORDER BY 1
    """)
    indexes = db.execute(
        "SELECT indexname,indexdef FROM pg_indexes WHERE tablename='orders' ORDER BY 1"
    )
    return constraints, indexes


def test_single_head_parent_and_upgrade_preserves_existing_rows(scratch):
    cfg = Config(str(migration.API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(migration.API_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert script.get_revision(SUCCESSOR).down_revision == REVISION
    assert script.get_revision(REVISION).down_revision == PREVIOUS

    scratch.migrate(target=PREVIOUS)
    seed(scratch)
    before = values(scratch)
    constraints, indexes = definitions(scratch)
    scratch.migrate(target=REVISION)
    assert scratch.marker() == REVISION
    assert values(scratch) == before
    assert scratch.execute(
        "SELECT count(guest_access_digest),count(guest_idempotency_digest),"
        "count(legal_terms_version),count(legal_privacy_version),count(legal_accepted_at) "
        "FROM orders"
    ) == [(0, 0, 0, 0, 0)]
    assert scratch.execute(
        "SELECT attnotnull FROM pg_attribute "
        "WHERE attrelid='orders'::regclass AND attname='user_id'"
    ) == [(False,)]
    after_constraints, after_indexes = definitions(scratch)
    owner = [row for row in after_constraints if row[0] == "orders_owner_ck"]
    assert len(owner) == 1
    assert [row for row in after_constraints if row[0] != "orders_owner_ck"] == constraints
    assert {name for name, _ in after_indexes} - {name for name, _ in indexes} == {
        "uq_orders_guest_access_digest",
        "uq_orders_guest_idempotency_digest",
    }
    assert [row for row in after_indexes if not row[0].startswith("uq_orders_guest_")] == indexes
    for name, definition in after_indexes:
        if name.startswith("uq_orders_guest_"):
            assert definition.startswith("CREATE UNIQUE INDEX")
            assert "NULLS NOT DISTINCT" not in definition
    check_parity(scratch)


def test_owner_check_and_unique_digests(scratch):
    scratch.migrate(target=REVISION)
    seed(scratch)
    scratch.execute(GUEST_INSERT, (801, "G1-guest-1", 0, *COMPLETE))
    # A guest row without an idempotency digest and owned rows: NULL digests coexist.
    scratch.execute(GUEST_INSERT, (802, "G1-guest-2", 0, "c" * 64, None, *COMPLETE[2:]))
    scratch.execute(GUEST_INSERT, (803, "G1-guest-3", 0, "d" * 64, None, *COMPLETE[2:]))
    refused = {
        "no_digest": (0, None, "e" * 64, *COMPLETE[2:]),
        "points": (100, "e" * 64, "f" * 64, *COMPLETE[2:]),
        "no_terms": (0, "e" * 64, "f" * 64, None, *COMPLETE[3:]),
        "no_privacy": (0, "e" * 64, "f" * 64, COMPLETE[2], None, COMPLETE[4]),
        "no_accepted_at": (0, "e" * 64, "f" * 64, COMPLETE[2], COMPLETE[3], None),
    }
    for case, row in refused.items():
        with pytest.raises(psycopg2.errors.CheckViolation):
            scratch.execute(GUEST_INSERT, (810, f"G1-{case}", *row))
    with pytest.raises(psycopg2.errors.UniqueViolation):
        scratch.execute(GUEST_INSERT, (811, "G1-dup-access", 0, "a" * 64, "9" * 64, *COMPLETE[2:]))
    with pytest.raises(psycopg2.errors.UniqueViolation):
        scratch.execute(GUEST_INSERT, (812, "G1-dup-key", 0, "9" * 64, "b" * 64, *COMPLETE[2:]))
    assert scratch.execute("SELECT count(*) FROM orders WHERE user_id IS NULL") == [(3,)]
    assert scratch.execute("SELECT count(*) FROM orders WHERE user_id IS NOT NULL") == [(2,)]


@pytest.mark.parametrize("state", ["guest", *NEW_COLUMNS])
def test_populated_downgrade_refuses_before_any_change(scratch, state):
    scratch.migrate(target=REVISION)
    seed(scratch)
    if state == "guest":
        scratch.execute(GUEST_INSERT, (801, "G1-guest-1", 0, *COMPLETE))
    else:
        value = "2026-10-27T10:00:00+00:00" if state == "legal_accepted_at" else "x"
        scratch.execute(f"UPDATE orders SET {state}=%s WHERE id=701", (value,))  # noqa: S608
    before = scratch.snapshot()
    assert "Guest order downgrade refused" in scratch.migrate("downgrade", PREVIOUS, success=False)
    assert scratch.marker() == REVISION
    assert scratch.snapshot() == before


def test_empty_downgrade_restores_predecessor_then_reupgrades(scratch):
    scratch.migrate(target=PREVIOUS)
    seed(scratch)
    predecessor = scratch.snapshot()
    scratch.migrate(target=REVISION)
    scratch.migrate("downgrade", PREVIOUS)
    assert scratch.marker() == PREVIOUS
    assert scratch.snapshot() == predecessor
    scratch.migrate(target=REVISION)
    assert scratch.marker() == REVISION
    check_parity(scratch)


def test_lock_timeout_leaves_no_partial_schema(scratch):
    scratch.migrate(target=PREVIOUS)
    before = scratch.snapshot()
    with migration.blocker(scratch) as blocker, ThreadPoolExecutor(max_workers=1) as pool:
        with blocker.cursor() as cur:
            cur.execute("LOCK TABLE orders IN ACCESS SHARE MODE")
        output = pool.submit(scratch.migrate, target=REVISION, success=False).result(timeout=60)
    assert "lock timeout" in output
    assert scratch.marker() == PREVIOUS
    assert scratch.snapshot() == before
