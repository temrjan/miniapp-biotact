"""Run only inside an owned disposable rootless container with /work and /helpers."""

# Commands use fixed executables/arguments and explicitly selected owned fixture/artifact paths.
# ruff: noqa: S603

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/helpers")
import packages

WORK = Path("/work")
UV = WORK / "uv"


def run(args, **kwargs):
    print("COMMAND", json.dumps([str(x) for x in args]), flush=True)
    return subprocess.run([str(x) for x in args], check=True, text=True, **kwargs)


def seed(name, pip):
    path = WORK / name
    run([sys.executable, "-m", "venv", "--without-pip", path])
    wheels = [
        WORK
        / ("certifi-2025.11.12-py3-none-any.whl" if pip else "certifi-2026.7.22-py3-none-any.whl")
    ]
    if pip:
        wheels += [WORK / "pip-26.2.1-py3-none-any.whl"]
    run(
        [
            UV,
            "--no-config",
            "--no-cache",
            "--offline",
            "pip",
            "install",
            "--python",
            path / "bin/python",
            "--no-index",
            "--no-deps",
            *wheels,
        ]
    )
    site = next(path.glob("lib/python*/site-packages"))
    marker = site / "tls_sentinel-1.0.dist-info"
    marker.mkdir()
    (marker / "METADATA").write_text("Metadata-Version: 2.1\nName: tls-sentinel\nVersion: 1.0\n")
    (marker / "WHEEL").write_text(
        "Wheel-Version: 1.0\nGenerator: tls-fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    )
    (marker / "RECORD").write_text("")
    (path / "bin/sentinel").write_text("#!/bin/sh\nexit 0\n")
    (path / "bin/sentinel").chmod(0o751)
    (path / "sentinel-link").symlink_to("bin/sentinel")
    if not pip:
        run(["groupadd", "--gid", "986", "tls-team-deploy"])
        run(["useradd", "--uid", "997", "--gid", "986", "--no-create-home", "tls-team-deploy"])
        for base, directories, files in os.walk(path):
            for entry in [Path(base), *(Path(base) / n for n in directories + files)]:
                os.lchown(entry, 997, 986)
    return path, site


for name, with_pip in [("shop-fixture-r7", True), ("team-fixture-r7", False)]:
    path, site = seed(name, with_pip)
    baseline = packages.environment(path)
    root = run(
        [
            path / "bin/python",
            "-B",
            "-c",
            "import ssl,certifi; print(any('emSign ECC Root CA - G3' in str(c['subject']) for c in ssl.create_default_context(cafile=certifi.where()).get_ca_certs()))",
        ],
        capture_output=True,
    )
    assert root.stdout.strip() == "True", "original vulnerable store must reproduce"
    packages.archive(path, WORK / (name + ".tar"))
    if with_pip:
        (site / "pip/__init__.py").write_text(
            "raise RuntimeError('synthetic partial pip install')\n"
        )
        failed = subprocess.run(
            [str(path / "bin/python"), "-B", "-m", "pip", "--version"],
            capture_output=True,
            text=True,
        )
        assert failed.returncode != 0
        assert "synthetic partial" in failed.stderr
        print("BROKEN_PIP_REPRODUCED", failed.returncode, flush=True)
        packages.repair(path, WORK, "pip")
        run([path / "bin/python", "-B", "-m", "pip", "check"])
    packages.repair(path, WORK, "certifi")
    packages.compare(
        baseline, packages.environment(path), ["pip", "certifi"] if with_pip else ["certifi"]
    )
    assert with_pip == ("pip" in packages.environment(path)["packages"])
    # Reject an incorrect path before any restoration and preserve the fixed env.
    try:
        packages.restore(path.with_name("wrong-original-path"), WORK / (name + ".tar"))
    except ValueError:
        print("WRONG_PATH_REFUSED", flush=True)
    else:
        raise AssertionError("wrong-path restore allowed")
    if with_pip:
        metadata = next(site.glob("pip-*.dist-info"))
        metadata.rename(WORK / (name + "-damaged-pip-metadata"))
        try:
            packages.repair(path, WORK, "pip")
        except ValueError:
            print("MISSING_METADATA_REFUSED_REQUIRES_ARCHIVE_THEN_FORWARD_REPAIR", flush=True)
        else:
            raise AssertionError("unknown package map was silently accepted")
    packages.restore(path, WORK / (name + ".tar"))
    assert packages.environment(path) == baseline
    # Old archive is never an allowed runnable fallback: repair while still stopped.
    for distribution in ["pip", "certifi"] if with_pip else ["certifi"]:
        packages.repair(path, WORK, distribution)
    packages.compare(
        baseline, packages.environment(path), ["pip", "certifi"] if with_pip else ["certifi"]
    )
    root = run(
        [
            path / "bin/python",
            "-B",
            "-c",
            "import ssl,certifi; print(any('emSign ECC Root CA - G3' in str(c['subject']) for c in ssl.create_default_context(cafile=certifi.where()).get_ca_certs()))",
        ],
        capture_output=True,
    )
    assert root.stdout.strip() == "False"
    if with_pip:
        run([path / "bin/python", "-B", "-m", "pip", "check"])
    else:
        no_pip = subprocess.run(
            [str(path / "bin/python"), "-B", "-m", "pip", "--version"], capture_output=True
        )
        assert no_pip.returncode != 0
        print("TEAM_STAYS_PIPLESS", flush=True)
print("PACKAGE_ARCHIVE_FORWARD_REPAIR_PASSED", flush=True)
