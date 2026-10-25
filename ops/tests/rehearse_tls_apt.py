"""Run the actual CA helper inside a new offline Ubuntu 24.04 rootless fixture.

The fixture must have APT 2.8.3, Python 3.12, strace, needrestart and the signed
Ubuntu baseline ca-certificates 20240203. Mount helpers/artifacts read-only and a
new output directory read-write. No host or production package manager is used.
"""

# Commands are fixed fixture tools or explicitly selected read-only helper paths.
# ruff: noqa: S603

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

CA = "ca-certificates_20260816ubuntu0.24.04.1_all.deb"
FIXED = "20260816ubuntu0.24.04.1"


def command(args: list[str]) -> str:
    return subprocess.check_output(args, text=True, timeout=60)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def package_map() -> str:
    return command(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${db:Status-Abbrev}\n"])


def identity(pid: int) -> str:
    return (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[19]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("helpers", type=Path)
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("expected", choices=("red", "green"))
    args = parser.parse_args()
    output = args.output.resolve()
    assert output.is_dir(), "use new owned output"
    assert not list(output.iterdir()), "use new owned output"
    assert os.getuid() == 0
    apt_version = command(["apt-get", "--version"]).splitlines()[0]
    assert apt_version == "apt 2.8.3 (amd64)"
    assert command(["dpkg-query", "-W", "-f=${Version}", "ca-certificates"]) == "20240203"
    assert not command(["dpkg", "--audit"])
    assert not list(Path("/etc/ca-certificates/update.d").iterdir())
    assert not list(Path("/usr/local/share/ca-certificates").rglob("*.crt"))

    # Production's saved indexes contain only the old CA. Empty fixture indexes
    # isolate that same local-file acquisition path without deleting cached data.
    Path("/run/tls-fixture-lists").mkdir()
    Path("/etc/apt/apt.conf.d/99-tls-fixture-only").write_text(
        'Dir::State::lists "/run/tls-fixture-lists";\n'
        'Dir::Etc::sourcelist "/dev/null";\nDir::Etc::sourceparts "-";\n'
    )

    artifacts = output / "artifacts"
    artifacts.mkdir()
    for name in (CA, "pip-26.2.2-py3-none-any.whl", "certifi-2026.10.19-py3-none-any.whl", "uv"):
        shutil.copyfile(args.artifacts / name, artifacts / name)
    inputs = [
        *sorted(Path("/etc/apt/apt.conf.d").iterdir()),
        Path("/etc/needrestart/needrestart.conf"),
    ]
    (artifacts / "apt-inputs.sha256").write_text(
        "".join(f"{digest(path)}  {path}\n" for path in inputs if path.is_file())
    )
    selection = Path("/etc/ca-certificates.conf")
    text = selection.read_text()
    assert "\nmozilla/ACCVRAIZ1.crt\n" in text
    selection.write_text(text.replace("\nmozilla/ACCVRAIZ1.crt\n", "\n!mozilla/ACCVRAIZ1.crt\n"))
    subprocess.run(
        ["/usr/sbin/update-ca-certificates"], check=True, capture_output=True, timeout=60
    )

    # This real socket and executable prove sensitivity of the trace channels.
    control = subprocess.run(
        [
            "/usr/bin/strace",
            "-f",
            "-o",
            str(output / "control.trace"),
            "-e",
            "trace=process,network",
            "/usr/bin/python3",
            "-I",
            "-B",
            "-c",
            "import socket,subprocess; s=socket.socket(); s.connect_ex(('127.0.0.1',9)); "
            "s.close(); subprocess.run(['/usr/bin/true'],check=True)",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    (output / "control.log").write_text(control.stdout + control.stderr)
    control_trace = (output / "control.trace").read_text()
    assert "socket(AF_INET," in control_trace
    assert 'execve("/usr/bin/true",' in control_trace

    before = package_map()
    (output / "packages.before").write_text(before)
    store_before = digest(Path("/etc/ssl/certs/ca-certificates.crt"))
    hooks_before = {str(path): digest(path) for path in inputs if path.is_file()}
    dpkg_log = Path("/var/log/dpkg.log")
    log_start = dpkg_log.stat().st_size
    sentinel = subprocess.Popen(["/usr/bin/sleep", "300"])
    try:
        sentinel_identity = identity(sentinel.pid)
        receipt = output / "receipt"
        result = subprocess.run(
            [
                "/usr/bin/strace",
                "-f",
                "-v",
                "-s",
                "8192",
                "-o",
                str(output / "helper.trace"),
                "-e",
                "trace=process,network",
                "/usr/bin/bash",
                str(args.helpers / "ca.sh"),
                str(artifacts),
                str(receipt),
            ],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        (output / "helper.log").write_text(result.stdout + result.stderr)
        after = package_map()
        (output / "packages.after").write_text(after)
        delta = dpkg_log.read_text()[log_start:]
        (output / "dpkg.delta.log").write_text(delta)
        trace = (output / "helper.trace").read_text()
        assert (receipt / "apt-install.log").exists(), result.stdout + result.stderr
        install_log = (receipt / "apt-install.log").read_text()
        simulation = (receipt / "apt-simulation.log").read_text()
        assert "Inst ca-certificates [20240203] (" + FIXED in simulation
        assert "1 upgraded, 0 newly installed, 0 to remove" in simulation
        assert "socket(AF_INET," not in trace
        assert "socket(AF_INET6," not in trace
        assert not re.search(r'execve\("[^"\n]*/(?:systemctl|service|invoke-rc.d)"', trace)
        assert identity(sentinel.pid) == sentinel_identity
        assert sentinel.poll() is None
        assert not command(["dpkg", "--audit"])
        assert {str(path): digest(path) for path in inputs if path.is_file()} == hooks_before
        assert "!mozilla/ACCVRAIZ1.crt" in selection.read_text().splitlines()
        assert not command(
            [
                "dpkg-query",
                "-W",
                "-f=${Triggers-Pending}${Triggers-Awaited}",
                "ca-certificates",
            ]
        )
        if args.expected == "red":
            assert result.returncode == 100
            assert "Internal Error, Pathname to install is not absolute" in install_log
            assert before == after
            assert not delta
            assert store_before == digest(Path("/etc/ssl/certs/ca-certificates.crt"))
            assert not (receipt / "packages.after").exists()
        else:
            assert result.returncode == 0, result.stdout + result.stderr + install_log
            expected = before.replace(
                "ca-certificates\t20240203\tii ", f"ca-certificates\t{FIXED}\tii "
            )
            assert before != expected
            assert after == expected
            assert f"Unpacking ca-certificates ({FIXED})" in install_log
            assert f"Setting up ca-certificates ({FIXED})" in install_log
            assert f"Processing triggers for ca-certificates ({FIXED})" in install_log
            assert "upgrade ca-certificates:all 20240203 " + FIXED in delta
            assert all(
                "ca-certificates:all" in line or "startup" in line for line in delta.splitlines()
            )
            assert store_before != digest(Path("/etc/ssl/certs/ca-certificates.crt"))
            assert '"NEEDRESTART_MODE=l"' in trace
            assert "/usr/sbin/needrestart" in trace
            archived = receipt / "apt-archives" / CA
            assert digest(archived) == digest(artifacts / CA)
            assert archived.stat().st_uid == 0
            assert archived.stat().st_mode & 0o777 == 0o444
        summary: dict[str, Any] = {
            "expected": args.expected,
            "apt": apt_version,
            "helper_exit": result.returncode,
            "helper_sha256": digest(args.helpers / "ca.sh"),
            "ca_sha256": digest(artifacts / CA),
            "before_version": "20240203",
            "after_version": command(
                [
                    "dpkg-query",
                    "-W",
                    "-f=${Version}",
                    "ca-certificates",
                ]
            ),
            "complete_map_expected": True,
            "triggers_pending_awaited": "",
            "dpkg_audit": "",
            "no_network_socket": True,
            "no_restart_command": True,
            "sentinel_identity_unchanged": True,
            "explicit_deselection_retained": True,
            "fixture_hooks_unchanged": True,
            "sensitive_trace_controls": True,
            "route_table": Path("/proc/net/route").read_text(),
            "limits": "Isolated signed Ubuntu 20240203 baseline, not live 20260601; fixture hooks/selection differ. No live services or production acceptance.",
        }
        (output / "result.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary, indent=2), flush=True)
    finally:
        sentinel.terminate()
        sentinel.wait(timeout=5)


if __name__ == "__main__":
    main()
