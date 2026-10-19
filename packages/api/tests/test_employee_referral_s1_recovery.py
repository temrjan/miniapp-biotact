"""Explicit paired proof using the prepared worker bytes and committed metadata.

Set S1_ROLLBACK_COMMIT to the delivered prepared SHA when checking an S1 checkout.
The selected worker must match these bytes; no second checkout is required.
"""

import asyncio
import importlib.util
import os
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.employee_referrals import worker
from tests.test_employee_referral_maintenance_health import configs
from tests.test_employee_referral_maintenance_health import probe_database as probe_database
from tests.test_employee_referral_maintenance_health import probe_sessions as probe_sessions
from tests.test_employee_referral_s1_compatibility import retained_schema as retained_schema

SOURCE = os.environ.get("S1_CONTROLLER_SOURCE", "")
ROLLBACK = os.environ.get("S1_ROLLBACK_COMMIT", "HEAD")
PREPARED_TREE = "97314e154042d8bdbeb62e7c77d9033ff1dcab94"
pytestmark = pytest.mark.skipif(
    not SOURCE, reason="Explicit S1 recovery pairing requires reviewed S1_CONTROLLER_SOURCE"
)
SHOP = Path(__file__).resolve().parents[3]


def source_git(*args: str) -> bytes:
    return subprocess.check_output(  # noqa: S603 - fixed read-only Git fixture calls
        ["/usr/bin/git", "-C", str(SHOP), *args]
    )


@pytest.fixture
def paired_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe_database: str
) -> tuple[ModuleType, Any]:
    source = Path(SOURCE)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    spec = importlib.util.spec_from_file_location("s1_controller_pair", source)
    assert spec is not None
    assert spec.loader is not None
    control = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = control
    spec.loader.exec_module(control)
    assert re.fullmatch(r"HEAD|[0-9a-f]{40}", ROLLBACK)
    selected = source_git("rev-parse", "--verify", f"{ROLLBACK}^{{commit}}").decode().strip()
    assert source_git("rev-parse", f"{selected}^{{tree}}").decode().strip() == PREPARED_TREE
    assert Path(worker.__file__).read_bytes() == source_git(
        "show", f"{selected}:packages/api/src/employee_referrals/worker.py"
    )
    names = (
        source_git("ls-tree", "-r", "--name-only", selected, "--", "packages/api/alembic/versions/")
        .decode()
        .splitlines()
    )
    predecessor_head = control.migration_head(
        [
            source_git("show", f"{selected}:{name}").decode()
            for name in names
            if name.endswith(".py") and not name.endswith("/__init__.py")
        ]
    )
    assert predecessor_head == "a8f6d2c93017"
    # Read the selected real Git graph instead of the newer checkout's graph.
    monkeypatch.setattr(worker, "migration_head", lambda: predecessor_head)
    library = sys.modules["referral_admission"]
    assert Path(library.__file__).resolve() == source.parent / "referral_admission.py"
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

    class Backend(control.Backend):
        def run(self, args: list[str], **_kwargs: object) -> str:
            if args[:3] == ["/usr/bin/git", "-C", "/var/www/biotact"]:
                # Runtime identity and candidate metadata use the real prepared object.
                git_args = args[3:]
                if git_args == ["rev-parse", "HEAD"]:
                    git_args = ["rev-parse", selected]
                return source_git(*git_args).decode().strip()
            if args[0] == "/usr/bin/curl":
                return '{"status":"healthy"}'
            raise AssertionError(args)

        def probe(self, role: str, since: float | None = None) -> dict[str, object]:
            if role == "portal":
                # Only external portal issuance/PM2/systemd IO is controlled.
                return {
                    "schema": "portal_fixture",
                    "schema_ready": True,
                    "ready": True,
                    "leased": 0,
                    "confirmation_seconds": 300,
                    "stored_confirmation_seconds": 0,
                }

            async def real_shop() -> dict[str, object]:
                engine = create_async_engine(
                    probe_database,
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

            return asyncio.run(real_shop())

        def workers(self) -> dict[str, object]:
            return {"shop": control.Identity.current(), "portal": control.Identity.current()}

        def attest(self, _workers: object) -> None:
            # External live registry observation; canonical state/locks/ownership are real.
            return None

    return control, Backend()


async def snapshot(sessions: Any) -> list[tuple[object, ...]]:
    async with sessions() as session:
        return [
            tuple(r)
            for r in await session.execute(
                text(
                    "SELECT (SELECT jsonb_agg(to_jsonb(c) ORDER BY id)::text FROM categories c), "
                    "(SELECT string_agg(version_num, ',' ORDER BY version_num) FROM alembic_version), "
                    "(SELECT attnotnull FROM pg_attribute WHERE attrelid='categories'::regclass "
                    "AND attname='is_purchasable')"
                )
            )
        ]


async def test_actual_predecessor_worker_and_guard_resume_retained_schema(
    paired_controller: tuple[ModuleType, Any],
    retained_schema: Any,
) -> None:
    control, backend = paired_controller
    async with retained_schema.begin() as session:
        await session.execute(
            text(
                "INSERT INTO categories(name,slug,sort_order,is_active,is_purchasable) VALUES "
                "('Synthetic true','recovery-true',0,true,true),"
                "('Synthetic false','recovery-false',0,true,false)"
            )
        )
    before = await snapshot(retained_schema)
    controller = control.Controller(backend)
    token = "a" * 32
    sha = await asyncio.to_thread(
        backend.run, ["/usr/bin/git", "-C", "/var/www/biotact", "rev-parse", "HEAD"]
    )

    def recover_after_cutover_failure() -> None:
        controller.begin("shop", token, os.getpid())
        assert not control.read_state().admits()
        controller.pause("shop", token)
        controller.compatible("shop", token, sha)
        fingerprint = backend.fingerprint()
        assert fingerprint["shop_schema"] == "b7e31c904a62"
        controller.finish("shop", token)
        assert control.read_state() is None

    await asyncio.to_thread(recover_after_cutover_failure)
    assert await snapshot(retained_schema) == before


async def test_dead_owner_with_native_drift_keeps_admission_closed(
    paired_controller: tuple[ModuleType, Any],
    retained_schema: Any,
) -> None:
    control, backend = paired_controller
    controller = control.Controller(backend)
    await asyncio.to_thread(controller.begin, "shop", "a" * 32, os.getpid())
    state = control.read_state()
    receiver = await asyncio.to_thread(backend.fingerprint)
    dead = control.Identity(99999999, state.owner.boot, state.owner.ticks)
    control.save_state(replace(state, owner=dead, receiver=receiver))
    async with retained_schema.begin() as session:
        await session.execute(
            text("ALTER TABLE categories ALTER COLUMN is_purchasable DROP NOT NULL")
        )
    with pytest.raises(ValueError, match="schema"):
        await asyncio.to_thread(controller.recover)
    saved = control.read_state()
    assert saved.phase == "failed"
    assert not saved.admits()
