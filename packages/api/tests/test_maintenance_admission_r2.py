"""Shop bridge corrupted-file regressions without external artifacts."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.employee_referrals import maintenance


async def test_dangling_library_parent_is_not_absent_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "dangling-library-parent"
    parent.symlink_to(tmp_path / "absent-directory")
    monkeypatch.setattr(maintenance, "LIBRARY", parent / "library.py")
    monkeypatch.setattr(maintenance, "_loaded", None)
    monkeypatch.setattr(maintenance, "Path", lambda _path: tmp_path / "absent-state")
    async with maintenance.admission("shop") as allowed:
        assert allowed is False


def test_fifo_installed_library_rejects_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "library.py"
    os.mkfifo(fifo, mode=0o644)
    script = r"""
import os, sys
from pathlib import Path
from src.employee_referrals import maintenance
original = Path.lstat
def trusted_lstat(self):
    values = list(original(self))
    values[4] = 0
    return os.stat_result(values)
Path.lstat = trusted_lstat
maintenance.LIBRARY = Path(sys.argv[1])
print("started", flush=True)
try:
    maintenance.provider()
except (ValueError, OSError):
    print("rejected", flush=True)
else:
    print("accepted", flush=True)
"""
    process = subprocess.Popen(  # noqa: S603 - isolated bounded interpreter probe.
        [sys.executable, "-c", script, str(fifo)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        try:
            stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=3)
            raise AssertionError(
                f"FIFO library blocked before validation: {stdout} {stderr}"
            ) from None
        assert process.returncode == 0, stderr
        assert stdout.splitlines() == ["started", "rejected"]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
