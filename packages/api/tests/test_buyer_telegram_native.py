"""TG1 additive native schema, retained S3 state and forward-only populated storage."""

import os
import sys

import psycopg2
import pytest

from tests import test_category_schema_migration as migration
from tests.test_buyer_auth_native import check_parity

scratch = migration.scratch
OLD = "c4f2a91e8d63"
TG1 = "d8a14f90bc21"
HEAD = "c8f2b4a91e30"  # Video banners media columns, the current single head.


def test_s3_preserved_tg1_native_constraints_and_populated_refusal(scratch):
    scratch.migrate(target=OLD)
    scratch.seed()
    scratch.execute("INSERT INTO buyer_auth_limits VALUES ('retained','[1]',now())")
    before = scratch.snapshot()
    scratch.migrate(target=TG1)
    assert scratch.marker() == TG1
    # Dropping only the still-empty additive table in a transaction gives an exact
    # comparison of ALL predecessor schema/rows, including archive exclusions/S3.
    scratch.conn.autocommit = False
    try:
        scratch.execute("DROP TABLE buyer_telegram_requests")
        assert scratch.snapshot() == before
    finally:
        scratch.conn.rollback()
        scratch.conn.autocommit = True
    scratch.migrate(target=HEAD)  # Model parity is defined at the current head.
    check_parity(scratch)
    scratch.execute("""INSERT INTO buyer_telegram_requests
        (id,browser_digest,request_key,purpose,state,created_at,expires_at,delivery)
        VALUES ('a','b','c','login','pending',now(),now(),'not_requested')""")
    before = scratch.snapshot()
    with pytest.raises(psycopg2.errors.UniqueViolation):
        scratch.execute("""INSERT INTO buyer_telegram_requests
        (id,browser_digest,request_key,purpose,state,created_at,expires_at,delivery)
        VALUES ('d','b','c','login','pending',now(),now(),'not_requested')""")
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        scratch.execute("UPDATE buyer_telegram_requests SET completed_user_id=999999")
    assert "Populated buyer Telegram downgrade refused" in scratch.migrate(
        "downgrade", OLD, success=False
    )
    assert scratch.marker() == HEAD
    assert scratch.snapshot() == before


def test_native_full_empty_downgrade_upgrade_parity(scratch):
    scratch.migrate(target=HEAD)
    check_parity(scratch)
    scratch.migrate("downgrade", "base")
    scratch.migrate(target=HEAD)
    check_parity(scratch)


def test_tg1_actual_controller_refuses_s3_and_accepts_prepared_new_head(
    scratch, tmp_path, monkeypatch
):
    # The actual controller is a read-only external input. The candidate remains
    # uncommitted: feed its exact migration metadata through the controller's Git
    # transport boundary, not another checkout or a claimed committed revision.
    from pathlib import Path

    from src.employee_referrals import worker
    from tests import test_buyer_auth_recovery as recovery

    source = os.environ.get("TG1_CONTROLLER_SOURCE")
    if not source:
        pytest.skip("Explicit TG1_CONTROLLER_SOURCE required for local paired recovery")
    control = recovery.load("tg1_trusted_controller", Path(source))
    library = sys.modules["referral_admission"]
    monkeypatch.setattr(library.trusted_directory, "__defaults__", (os.geteuid(),))
    monkeypatch.setattr(library.trusted_fd, "__kwdefaults__", {"uid": os.geteuid()})
    monkeypatch.setattr(library.read_json, "__defaults__", (os.geteuid(),))
    root = tmp_path / "control"
    root.mkdir(mode=0o755)
    for name in ("operation.lock", "delivery.lock"):
        (root / name).touch(mode=0o644)
    monkeypatch.setattr(library, "CONTROL_ROOT", root)
    monkeypatch.setattr(control, "CONTROL_ROOT", root)
    scratch.migrate(target=HEAD)
    scratch.execute("""INSERT INTO buyer_telegram_requests
        (id,browser_digest,request_key,purpose,state,created_at,expires_at,delivery)
        VALUES ('a','b','c','login','cancelled',now(),now(),'uncertain')""")
    before = scratch.snapshot()
    prefix = "packages/api/alembic/versions/"
    names = sorted(
        p.name
        for p in (migration.API_ROOT / "alembic/versions").glob("*.py")
        if p.name != "__init__.py"
    )
    sources = {
        prefix + name: (migration.API_ROOT / "alembic/versions" / name).read_text()
        for name in names
    }
    for name in ("maintenance.py", "worker.py"):
        sources["packages/api/src/employee_referrals/" + name] = (
            migration.API_ROOT / "src/employee_referrals" / name
        ).read_text()
    migration_sources = {
        name: source for name, source in sources.items() if name.startswith(prefix)
    }
    prepared = "d" * 40
    old = recovery.git("rev-parse", "HEAD").strip()

    class Backend(control.Backend):
        def run(self, args, **kwargs):
            if args[:3] == ["/usr/bin/git", "-C", "/var/www/biotact"]:
                if args[3] == "ls-tree" and args[6] == prepared:
                    return "\n".join(migration_sources)
                if args[3] == "show" and args[4].startswith(prepared + ":"):
                    return sources[args[4].split(":", 1)[1]]
                return recovery.git(*args[3:]).strip()
            raise AssertionError(args)

        def probe(self, role, since=None):
            return {
                "schema": scratch.marker() if role == "shop" else "synthetic_portal",
                "schema_ready": True,
                "ready": True,
                "healthy": True,
                "leased": 0,
                "confirmation_seconds": 300,
                "stored_confirmation_seconds": 0,
            }

        def fingerprint(self):
            return {
                "shop_release": prepared,
                "shop_schema": scratch.marker(),
                "portal_release": "synthetic",
                "portal_schema": "synthetic_portal",
            }

        def workers(self):
            return {"shop": control.Identity.current(), "portal": control.Identity.current()}

        def attest(self, workers):
            pass

    backend = Backend()
    controller = control.Controller(backend)
    token = "b" * 32
    controller.begin("shop", token, os.getpid())
    controller.pause("shop", token)
    with pytest.raises(ValueError, match="Incompatible rollback schema"):
        controller.compatible("shop", token, old)
    assert control.read_state().phase == "paused"
    assert not control.read_state().admits()
    controller.compatible("shop", token, prepared)
    assert control.read_state().phase == "paused"
    assert control.migration_head(list(migration_sources.values())) == HEAD
    assert worker.migration_head() == HEAD
    assert scratch.snapshot() == before
    # Prepared-source admission does not assert live assets/process readiness.
    assets = recovery.load("tg1_release_assets", recovery.SHOP / "ops/shop-release-assets.py")
    project = tmp_path / "synthetic-assets"
    store = project / ".venv/shop-release-assets"
    store.mkdir(parents=True)
    for owner in assets.OWNERS:
        folder = project / owner / "dist"
        folder.mkdir(parents=True)
        (folder / "index.html").write_text("TG1 prepared synthetic bundle")
    saved = assets.capture(project, store, prepared)
    manifest = assets.load_snapshot(store, saved)
    for owner in assets.OWNERS:
        (project / owner / "dist/index.html").write_text("partial interrupted build")
    assets.restore(project, saved, manifest)
    assert {
        owner: assets.inventory(project / owner / "dist") for owner in assets.OWNERS
    } == manifest["artifacts"]
    controller.finish("shop", token)
    assert control.read_state() is None
