"""Event promotion migration (event-qr-discount S3 R-02) on real Alembic transactions.

``alembic check`` does not compare CHECK constraints or server defaults here (``alembic/env.py``
enables neither), so the constraints and the ``promotion_discount`` default are proved at runtime.
Each test owns a scratch database on the disposable server named by S1_TEST_DATABASE_URL.
"""

import psycopg2
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from tests import test_category_schema_migration as migration
from tests.test_buyer_auth_native import check_parity

scratch = migration.scratch
PREVIOUS = "b4f4c51eb14a"
REVISION = "87688cb444f4"
PROMOTION = (
    "INSERT INTO event_promotions (id,code,title,percent,starts_at,ends_at) "
    "VALUES (%s,%s,%s,%s,%s,%s)"
)
START, END = "2026-11-27T19:00:00+00:00", "2026-12-31T19:00:00+00:00"


def test_single_head_round_trip_and_existing_line_discount_is_zero(scratch):
    cfg = Config(str(migration.API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(migration.API_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    assert script.get_heads() == [REVISION]
    assert script.get_revision(REVISION).down_revision == PREVIOUS

    scratch.migrate(target=PREVIOUS)
    scratch.seed()
    before = scratch.snapshot()
    scratch.migrate(target=REVISION)
    assert scratch.execute("SELECT id, promotion_discount FROM order_items") == [(701, 0)]
    assert scratch.execute("SELECT id, promotion_id FROM orders") == [(701, None)]
    scratch.migrate("downgrade", PREVIOUS)
    assert scratch.marker() == PREVIOUS
    assert scratch.snapshot() == before
    scratch.migrate(target=REVISION)
    assert scratch.marker() == REVISION
    check_parity(scratch)


def test_constraints_refuse_invalid_rows(scratch):
    scratch.migrate(target=REVISION)
    scratch.seed()
    scratch.execute(PROMOTION, (1, "nutriforum2026", "NUTRIFORUM 2026", 5, START, END))
    for row in (
        (2, "p0", "T", 0, START, END),
        (3, "p91", "T", 91, START, END),
        (4, "empty-window", "T", 5, END, END),
        (5, "reversed", "T", 5, END, START),
        (6, "no-title", "", 5, START, END),
    ):
        with pytest.raises(psycopg2.errors.CheckViolation):
            scratch.execute(PROMOTION, row)
    with pytest.raises(psycopg2.errors.UniqueViolation):
        scratch.execute(PROMOTION, (7, "nutriforum2026", "T", 5, START, END))
    # The inclusive limits are accepted: percent 1 and 90, a code of 40, a title of 60.
    scratch.execute(PROMOTION, (8, "a" * 40, "x" * 60, 1, START, END))
    scratch.execute(PROMOTION, (9, "p90", "T", 90, START, END))

    grant = (
        "INSERT INTO event_promotion_grants (promotion_id,user_id,is_new_user) VALUES (1,701,true)"
    )
    scratch.execute(grant)
    with pytest.raises(psycopg2.errors.UniqueViolation):
        scratch.execute(grant)
    with pytest.raises(psycopg2.errors.CheckViolation):
        scratch.execute(
            "INSERT INTO event_promotion_scans (promotion_id,user_id,outcome) VALUES (1,701,'x')"
        )
    assert scratch.execute("SELECT count(*) FROM event_promotions") == [(3,)]
    assert scratch.execute("SELECT count(*) FROM event_promotion_grants") == [(1,)]
    assert scratch.execute("SELECT count(*) FROM event_promotion_scans") == [(0,)]
