"""Offline real-process current-alias regression; run in an owned Python container."""

# Controlled interpreter paths and subprocess arguments; no application imports.
# ruff: noqa: S603

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import packages

root = Path("/work/consumer-case")
backend = root / "releases/fixture/backend"
venv = backend / ".venv"
subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
(root / "current").symlink_to(root / "releases/fixture")
for label, executable in [
    ("canonical", venv / "bin/python"),
    ("current-alias", root / "current/backend/.venv/bin/python"),
    ("relative-current-alias", Path("../../../current/backend/.venv/bin/python")),
]:
    consumer = subprocess.Popen(
        [
            str(executable),
            "-I",
            "-B",
            "-c",
            'import time; print("ready",flush=True); time.sleep(60)',
        ],
        cwd=backend,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert consumer.stdout.readline().strip() == "ready"
        assert Path(f"/proc/{consumer.pid}/cwd").resolve() == backend
        assert not any(
            fd.resolve().is_relative_to(venv) for fd in Path(f"/proc/{consumer.pid}/fd").iterdir()
        )
        for operation in [
            lambda: packages.stopped(venv),
            lambda label=label: packages.archive(venv, root / (label + ".tar")),
            lambda: packages.repair(venv, root / "no-artifacts", "certifi"),
            lambda: packages.restore(venv, root / "no-archive.tar"),
        ]:
            try:
                operation()
            except ValueError as exc:
                detail = str(exc)
            else:
                raise AssertionError("live " + label + " consumer accepted")
            assert detail == "environment still in use by PID " + str(consumer.pid), detail
        assert consumer.poll() is None
        print("LIVE_CONSUMER_REFUSED_BY_STOP_ARCHIVE_REPAIR_RESTORE", label, flush=True)
    finally:
        consumer.terminate()
        consumer.wait(timeout=5)
        consumer.stdout.close()
unrelated = subprocess.Popen(
    [  # noqa: S607 - executable=sys.executable selects the real absolute binary.
        "unrelated: worker /nonexistent/title/argument",
        "-B",
        "-c",
        'import time; print("ready",flush=True); time.sleep(60)',
    ],
    executable=sys.executable,
    cwd=backend,
    stdout=subprocess.PIPE,
    text=True,
)
try:
    assert unrelated.stdout.readline().strip() == "ready"
    packages.stopped(venv)
    packages.archive(venv, root / "stopped.tar")
finally:
    unrelated.terminate()
    unrelated.wait(timeout=5)
    unrelated.stdout.close()
assert (root / "stopped.tar").is_file()
assert os.stat(root / "stopped.tar").st_mode & 0o777 == 0o600
print("FULLY_STOPPED_ARCHIVE_POSITIVE_CONTROL_PASSED", flush=True)
