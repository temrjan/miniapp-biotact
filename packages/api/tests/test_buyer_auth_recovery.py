"""S3 same-head forward recovery using actual trusted controller and native worker.

S3_CONTROLLER_SOURCE must explicitly select the reviewed external controller.
Only synthetic control state/assets are created, inside this project's test temp.
Source revisions are read from Git objects without creating another checkout.
"""

import asyncio
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.employee_referrals import worker
from tests import test_category_schema_migration as migration
from tests.test_employee_referral_maintenance_health import configs

scratch = migration.scratch
SOURCE = os.environ.get("S3_CONTROLLER_SOURCE", "")
SHOP = Path(__file__).resolve().parents[3]
OLD = "3a334397b64046f907e3b4afdffa7ba4eb83f348"
HEAD = "c4f2a91e8d63"
pytestmark = pytest.mark.skipif(
    not SOURCE, reason="Explicit reviewed S3_CONTROLLER_SOURCE required for paired recovery"
)


def git(*args):
    return subprocess.check_output(["/usr/bin/git", "-C", str(SHOP), *args], text=True)  # noqa: S603


def load(name, source):
    spec = importlib.util.spec_from_file_location(name, source)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def recovery(scratch, tmp_path, monkeypatch):
    candidate = git("rev-parse", "HEAD").strip()
    metadata = git(
        "ls-tree", "-r", "--name-only", candidate, "--", "packages/api/alembic/versions/"
    )
    if "c4f2a91e8d63_buyer_email_sessions.py" not in metadata:
        pytest.skip("Recovery pairing requires the saved S3 candidate commit")
    assert Path(worker.__file__).read_text() == git(
        "show", f"{OLD}:packages/api/src/employee_referrals/worker.py"
    )
    scratch.migrate(target=HEAD)
    monkeypatch.setattr(worker, "migration_head", lambda: HEAD)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    control = load("s3_trusted_controller", Path(SOURCE))
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
    portal = tmp_path / ("a" * 40)
    portal.mkdir()
    monkeypatch.setattr(
        control, "Path", lambda p: portal if p == "/var/www/biotact-team-app/current" else Path(p)
    )
    assets = load("s3_release_assets", SHOP / "ops/shop-release-assets.py")
    project = tmp_path / "synthetic-assets"
    store = project / ".venv/shop-release-assets"
    store.mkdir(parents=True)
    for owner in assets.OWNERS:
        dist = project / owner / "dist"
        dist.mkdir(parents=True)
        (dist / "index.html").write_text("S3 candidate synthetic assets")
        (dist / "app.js").write_text("S3 synthetic bundle")
    saved = assets.capture(project, store, candidate)
    manifest = assets.load_snapshot(store, saved)

    class Backend(control.Backend):
        def run(self, args, **kwargs):
            if args[:3] == ["/usr/bin/git", "-C", "/var/www/biotact"]:
                return git(*args[3:]).strip()
            if args[0] == "/usr/bin/curl":
                return '{"status":"healthy"}'
            raise AssertionError(args)

        def probe(self, role, since=None):
            if role == "portal":
                return {
                    "schema": "synthetic_portal",
                    "schema_ready": True,
                    "ready": True,
                    "leased": 0,
                    "confirmation_seconds": 300,
                    "stored_confirmation_seconds": 0,
                }

            async def observe():
                engine = create_async_engine(
                    scratch.url,
                    poolclass=NullPool,
                    connect_args={"server_settings": {"default_transaction_read_only": "on"}},
                )
                try:
                    result, _code = await worker.maintenance_health(
                        async_sessionmaker(engine), configs()
                    )
                    return result
                finally:
                    await engine.dispose()

            return asyncio.run(observe())

        def workers(self):
            return {"shop": control.Identity.current(), "portal": control.Identity.current()}

        def attest(self, workers):
            pass  # PM2/portal issuance are fixture boundaries; ownership/locks/controller/SQL are actual.

        def fresh(self, state):
            assert {
                owner: assets.inventory(project / owner / "dist") for owner in assets.OWNERS
            } == manifest["artifacts"]
            return super().fresh(state)

    return control, Backend(), assets, project, saved, manifest, candidate


def test_old_s2_refused_paused_then_exact_s3_source_assets_recovered(
    recovery, scratch, monkeypatch
):
    control, backend, assets, project, saved, manifest, candidate = recovery
    before = scratch.snapshot()
    controller = control.Controller(backend)
    token = "b" * 32
    controller.begin("shop", token, os.getpid())
    controller.pause("shop", token)
    with pytest.raises(ValueError, match="Incompatible rollback schema"):
        controller.compatible("shop", token, OLD)
    assert control.read_state().phase == "paused"
    assert not control.read_state().admits()
    # Same worker bytes with old revision metadata actually refuse the migrated native DB.
    with monkeypatch.context() as old:
        old.setattr(worker, "migration_head", lambda: "b7e31c904a62")
        assert backend.probe("shop")["schema_ready"] is False
    for owner in assets.OWNERS:
        (project / owner / "dist/index.html").write_text("interrupted partial candidate build")
    controller.compatible("shop", token, candidate)
    assert control.read_state().phase == "paused"
    assets.restore(project, saved, manifest)
    controller.finish("shop", token)
    assert control.read_state() is None
    assert backend.probe("shop")["schema"] == HEAD
    assert scratch.snapshot() == before
