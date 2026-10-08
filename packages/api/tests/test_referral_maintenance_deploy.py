"""Execute the actual deployment Bash with isolated commands and filesystem."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def remote_script() -> str:
    text = (ROOT / ".github/workflows/deploy.yml").read_text()
    block = text.split("<< 'SCRIPT'\n", 1)[1].split("\n          SCRIPT", 1)[0]
    return "\n".join(line[10:] for line in block.splitlines())


def run_deploy(tmp_path: Path, failure: str = "") -> tuple[int, list[str]]:
    app = tmp_path / "app"
    for directory in (
        "packages/frontend",
        "admin-panel-v2",
        "packages/api/venv/bin",
        "packages/bot/venv/bin",
        "ops",
    ):
        (app / directory).mkdir(parents=True, exist_ok=True)
    for package in ("api", "bot"):
        (app / f"packages/{package}/venv/bin/activate").write_text("deactivate() { :; }\n")
    (app / "ops/employee-referral-release.sh").write_text(
        'echo "release:$1" >> "$TRACE"\n[ "$FAIL" != "release:$1" ]\n'
    )
    guard = tmp_path / "guard"
    guard.write_text("#!/bin/sh\nexit 0\n")
    guard.chmod(0o755)
    prefix = """
trace() { printf '%s\\n' "$1" >> "$TRACE"; [ "$FAIL" != "$1" ]; }
git() { trace "git:$1" || return 1; if [ "$1" = rev-parse ]; then printf '%040d\\n' 1; fi; }
pm2() { trace "pm2:$1:$2" || return 1; if [ "$1" = pid ]; then echo 7; fi; }
sudo() { shift 2; shift; trace "guard:$1"; }
npm() { trace "npm:$1"; }
pip() { trace "pip:$1"; }
alembic() { trace "migration:$1"; }
curl() { trace health; }
sleep() { :; }
"""
    script = (
        remote_script()
        .replace("/var/www/biotact", str(app))
        .replace("/usr/local/sbin/biotact-referral-maintenance", str(guard))
    )
    env = {**os.environ, "TRACE": str(tmp_path / "trace"), "FAIL": failure}
    result = subprocess.run(  # noqa: S603 - isolated Bash and command stubs only
        ["/usr/bin/bash", "-c", prefix + script],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    events = (tmp_path / "trace").read_text().splitlines()
    return result.returncode, events


def test_success_closes_guard_before_stops_and_finishes_after_checks(tmp_path: Path) -> None:
    code, events = run_deploy(tmp_path)
    assert code == 0
    assert (
        events.index("guard:begin")
        < events.index("pm2:stop:api")
        < events.index("git:fetch")
        < events.index("migration:upgrade")
    )
    assert events.index("release:check") < events.index("guard:finish")
    assert "guard:pause" not in events


@pytest.mark.parametrize(
    "failure",
    ["pm2:stop:api", "git:fetch", "npm:ci", "migration:upgrade", "health", "guard:finish"],
)
def test_failure_drains_and_checks_compatibility_before_rollback(
    tmp_path: Path, failure: str
) -> None:
    code, events = run_deploy(tmp_path, failure)
    assert code != 0
    assert "guard:pause" in events
    assert "guard:compatible" in events
    assert (
        events.index("guard:pause")
        < events.index("guard:compatible")
        < events.index("release:rollback")
    )


@pytest.mark.parametrize("failure", ["guard:begin", "guard:pause", "guard:compatible"])
def test_failed_guard_never_permits_unprotected_mutation(tmp_path: Path, failure: str) -> None:
    # Force a build error to enter rollback, while rejecting pause/compatibility.
    if failure == "guard:begin":
        code, events = run_deploy(tmp_path, failure)
        assert code != 0
        assert "pm2:stop:api" not in events
        assert "git:fetch" not in events
    else:
        # Two independently failing external boundaries, not a modified deploy script.
        original = remote_script()
        from unittest.mock import patch

        with patch(
            __name__ + ".remote_script",
            return_value=original.replace("npm ci --production=false", "false"),
        ):
            code, events = run_deploy(tmp_path, failure)
        assert code != 0
        assert "release:rollback" not in events
        assert events.count("guard:pause") == 1
