#!/usr/bin/env python3
"""F40 only: capture/close exact ingress, then freshly attest the pre-pause barrier.

No failure path restores open ingress. The gate has no owner/lease expiry.
"""

# Commands invoke fixed local Nginx/systemctl paths under the approved F40 boundary.

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from gate import BASE_SHA, pre_pause, render
from observe import REPLICA_SQL, SOURCE_SQL, TEAM, command, query

from packages import require

SITE = Path("/etc/nginx/sites-enabled/biotact")
PIDFILE = Path("/run/nginx.pid")
EXPECTED = {
    "/etc/nginx/nginx.conf": "990b0a8b2102ace14205beaa4f943de090f9116f743ee2d40ad5a0f8e825a59b",
    "/etc/nginx/sites-enabled/biotact": "d281f2213d64a39d36b5731a8b62d6e96b0e8d5a96be9cc6f276a31a0280cb72",
    "/etc/nginx/sites-enabled/biotact-team": "90a9ae936b2cff370d169737015138ffda147ccacaa495adf519ca50fc9e2ed7",
    "/etc/nginx/sites-enabled/multicard": "72bc26c081cc36995b361d236ffce1a07e07982ffa0c1fc2f57e3a72c7177f3b",
    "/etc/nginx/sites-enabled/voice": "10d560bbdd2a2a23bbbafb73eed23e57c82f2b8f003216af645c1d444bd7d527",
    "/etc/nginx/conf.d/security_headers.conf": "dfad0a035d009fac44b8e325c62e0d54429e24559df1d9ee40ba825cde3b73c3",
    "/etc/nginx/snippets/security_headers.conf": "706c943ed631bd916ed9c7b6060b3bf063a3f751c7e2c4091e7002822cf42573",
    "/etc/nginx/snippets/biotact_entry_security_headers.conf": "318bc6d474a487290c70aa1ebf2b0d7a79acfcb0a30ee83108d28a94d45d3738",
}


EXPECTED_INCLUDES = {
    "/etc/nginx/nginx.conf": "990b0a8b2102ace14205beaa4f943de090f9116f743ee2d40ad5a0f8e825a59b",
    "/etc/nginx/mime.types": "36c77daa2d85803e87c91ba723dd2bc6aaa6543ae4bd4a569034a7bf8bce464d",
    "/etc/nginx/conf.d/security_headers.conf": "dfad0a035d009fac44b8e325c62e0d54429e24559df1d9ee40ba825cde3b73c3",
    "/etc/nginx/sites-enabled/biotact": "d281f2213d64a39d36b5731a8b62d6e96b0e8d5a96be9cc6f276a31a0280cb72",
    "/etc/nginx/snippets/security_headers.conf": "706c943ed631bd916ed9c7b6060b3bf063a3f751c7e2c4091e7002822cf42573",
    "/etc/nginx/snippets/biotact_entry_security_headers.conf": "318bc6d474a487290c70aa1ebf2b0d7a79acfcb0a30ee83108d28a94d45d3738",
    "/etc/letsencrypt/options-ssl-nginx.conf": "5e21cc66989f26ec46116d979421e538131cf8ab33ffff3f682fbfe491b0ace8",
    "/etc/nginx/sites-enabled/biotact-team": "90a9ae936b2cff370d169737015138ffda147ccacaa495adf519ca50fc9e2ed7",
    "/etc/nginx/sites-enabled/multicard": "72bc26c081cc36995b361d236ffce1a07e07982ffa0c1fc2f57e3a72c7177f3b",
    "/etc/nginx/sites-enabled/voice": "10d560bbdd2a2a23bbbafb73eed23e57c82f2b8f003216af645c1d444bd7d527",
}


def process(pid: int) -> dict[str, str | int] | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if fields[0] in {"Z", "X", "x"}:
            return None
        return {"pid": pid, "ticks": fields[19]}
    except FileNotFoundError:
        return None


def workers(binding: dict[str, Any]) -> list[dict[str, Any]]:
    """Census the bound master; missing/changed process evidence refuses the barrier."""
    master = binding["master"]
    require(not Path(str(PIDFILE) + ".oldbin").exists(), "Nginx binary upgrade in progress")
    pid = int(PIDFILE.read_text())
    require(pid == master["pid"] and process(pid) == master, "Nginx master changed")
    require(
        Path(f"/proc/{pid}/cmdline").read_bytes().startswith(b"nginx: master process"),
        "unexpected Nginx master",
    )
    result = []
    for child in Path(f"/proc/{pid}/task/{pid}/children").read_text().split():
        identity = process(int(child))
        if identity is None:
            continue
        try:
            title = Path(f"/proc/{child}/cmdline").read_bytes()
        except FileNotFoundError:
            require(process(int(child)) is None, "Nginx child evidence unavailable")
            continue
        require(title.startswith(b"nginx: worker process"), "unaccounted Nginx child")
        if process(int(child)) == identity:
            result.append({**identity, "shutting_down": b"is shutting down" in title})
    require(process(pid) == master and int(PIDFILE.read_text()) == pid, "Nginx master changed")
    return result


def remaining_workers(binding: dict[str, Any]) -> list[dict[str, Any]]:
    current = workers(binding)
    remaining = [p for p in binding["barrier_workers"] if process(p["pid"]) == p]
    for row in current:
        identity = {"pid": row["pid"], "ticks": row["ticks"]}
        if row["shutting_down"] and identity not in remaining:
            remaining.append(identity)
    return remaining


def capture(directory: Path) -> None:
    require(not directory.exists(), "capture directory already exists")
    for name, sha in EXPECTED.items():
        require(
            hashlib.sha256(Path(name).read_bytes()).hexdigest() == sha,
            "ingress input changed: " + name,
        )
    require(
        SITE.stat().st_uid == 0
        and SITE.stat().st_gid == 0
        and SITE.stat().st_mode & 0o777 == 0o644,
        "site ownership/mode changed",
    )
    original = SITE.read_bytes()
    closed = render(original)
    master = process(int(PIDFILE.read_text()))
    require(master is not None, "Nginx master absent")
    inventory = workers({"master": master})
    config = subprocess.run(["/usr/sbin/nginx", "-T"], capture_output=True, check=True, timeout=5)
    included = re.findall(r"^# configuration file (.*):$", config.stdout.decode(), re.M)
    require(set(included) == set(EXPECTED_INCLUDES), "included ingress file set changed")
    require(
        all(
            hashlib.sha256(Path(n).read_bytes()).hexdigest() == sha
            for n, sha in EXPECTED_INCLUDES.items()
        ),
        "included ingress configuration changed",
    )
    directory.mkdir(mode=0o700)
    (directory / "original.conf").write_bytes(original)
    (directory / "closed.conf").write_bytes(closed)
    initial_lease = lease()
    require(
        initial_lease["minimum_valid_until"] - time.time() > 90,
        "initial renewal margin insufficient",
    )
    (directory / "lease.json").write_text(json.dumps(initial_lease, indent=2) + "\n")
    (directory / "binding.json").write_text(
        json.dumps(
            {
                "base_sha256": BASE_SHA,
                "closed_sha256": hashlib.sha256(closed).hexdigest(),
                "prior_effective_sha256": hashlib.sha256(config.stdout + config.stderr).hexdigest(),
                "site_resolved": str(SITE.resolve()),
                "uid": SITE.stat().st_uid,
                "gid": SITE.stat().st_gid,
                "mode": SITE.stat().st_mode & 0o777,
                "master": master,
                "old_workers": [{"pid": p["pid"], "ticks": p["ticks"]} for p in inventory],
                "phase": "captured",
            },
            indent=2,
        )
        + "\n"
    )


def close(directory: Path) -> None:
    binding = json.loads((directory / "binding.json").read_text())
    require(binding["phase"] == "captured", "close already attempted")
    workers(binding)
    require(str(SITE.resolve()) == binding["site_resolved"], "site path changed")
    require(
        hashlib.sha256(SITE.read_bytes()).hexdigest() == binding["base_sha256"],
        "ingress changed after capture",
    )
    config = subprocess.run(["/usr/sbin/nginx", "-T"], capture_output=True, check=True, timeout=5)
    require(
        hashlib.sha256(config.stdout + config.stderr).hexdigest()
        == binding["prior_effective_sha256"],
        "effective ingress changed",
    )
    latest = lease()
    require(
        latest["minimum_valid_until"] - time.time() > 90,
        "lease does not cover bounded reload/observation/stop budget",
    )
    (directory / "lease.json").write_text(json.dumps(latest, indent=2) + "\n")
    temporary = SITE.with_name(SITE.name + ".tls-closed")
    require(not temporary.exists(), "unexpected temporary site")
    temporary.write_bytes(render(SITE.read_bytes()))
    os.chmod(temporary, binding["mode"])
    os.chown(temporary, binding["uid"], binding["gid"])
    os.replace(temporary, SITE)
    # A failed syntax check/reload leaves the persistent closed candidate on disk;
    # the operator must protect old writers and restore a valid CLOSED config.
    try:
        command(["/usr/sbin/nginx", "-t"])
        workers(binding)
        binding["phase"] = "reload-requested"
        (directory / "binding.json").write_text(json.dumps(binding, indent=2) + "\n")
        command(["/usr/bin/systemctl", "reload", "nginx"])
    except Exception:
        from drain import stop_old_writer

        stop_old_writer(latest, emergency=True)
        raise


def gate_closed() -> bool:
    """Observe reload activation only. These probes never prove worker exit."""
    for host in ("app.biotact.uz", "admin.biotact.uz"):
        for path in ("/api/v1/orders", "/api/v1/orders/", "/api/v1/%6frders"):
            status = command(
                [
                    "curl",
                    "--path-as-is",
                    "--silent",
                    "--show-error",
                    "--max-time",
                    "2",
                    "--resolve",
                    host + ":443:127.0.0.1",
                    "--request",
                    "POST",
                    "--data",
                    "{}",
                    "--output",
                    "/dev/null",
                    "--write-out",
                    "%{http_code}",
                    "https://" + host + path,
                ]
            )
            if status != "423":
                return False
    return True


def preflight(directory: Path) -> dict[str, Any]:
    binding = json.loads((directory / "binding.json").read_text())
    require(binding["phase"] == "fenced", "Nginx generation fence incomplete")
    require(
        hashlib.sha256(SITE.read_bytes()).hexdigest() == binding["closed_sha256"],
        "closed configuration changed",
    )
    require(
        not remaining_workers(binding),
        "old Nginx workers still serve open ingress; keep K1 running",
    )
    require(gate_closed(), "gate not effective on both vhosts")
    result = lease()
    result["old_nginx_workers"] = remaining_workers(binding)
    pre_pause(result, time.time(), 10)
    return result


def lease() -> dict[str, Any]:
    health = json.loads(
        command(
            [
                "runuser",
                "-u",
                "biotact-team",
                "--",
                str(TEAM / "backend/.venv/bin/python"),
                "-B",
                "-m",
                "team_app",
                "referral-health",
                "--env-file",
                "/etc/biotact-team/app.env",
            ],
            TEAM / "backend",
        )
    )
    require(
        health.get("enabled") is True
        and health.get("ready") is True
        and health.get("configuration_valid") is True
        and not any(health["counts"]),
        "K1 renewal unhealthy",
    )
    observed = time.time()
    source = query("biotact_team_db", SOURCE_SQL)
    replica = query("biotact_db", REPLICA_SQL)
    require(set(source) == set(replica), "active campaign set incomplete")
    for key, wanted in source.items():
        require(
            wanted["blocked"] is False and wanted["ack_matches"] is True, "source K1 not confirmed"
        )
        actual = replica[key]
        require(actual["receipt_matches"] is True, "replica receipt absent")
        require(
            all(
                actual[k] == wanted[k]
                for k in ("revision", "content_sha256", "participants", "issued_at", "valid_until")
            ),
            "K1 replica differs",
        )
    result: dict[str, Any] = {
        "observed_at": observed,
        "minimum_valid_until": min(
            (r["valid_until"] for r in replica.values()), default=observed + 300
        ),
        "no_active_campaigns": not source,
        "gate_effective": True,
        "old_nginx_workers": [],
        "renewal_healthy": True,
    }
    return result


def wait_closed(directory: Path) -> dict[str, Any]:
    from drain import stop_old_writer

    binding = json.loads((directory / "binding.json").read_text())
    last_good = json.loads((directory / "lease.json").read_text())
    try:
        while True:
            current = lease()
            require(
                current["minimum_valid_until"] - time.time() > 75,
                "renewal margin below bounded observation/stop budget",
            )
            last_good = current
            (directory / "lease.json").write_text(json.dumps(last_good, indent=2) + "\n")
            require(
                hashlib.sha256(SITE.read_bytes()).hexdigest() == binding["closed_sha256"],
                "closed configuration changed",
            )
            inventory = workers(binding)
            if binding["phase"] == "reload-requested":
                if gate_closed():
                    # Nginx 1.24's single master cannot spawn an open-config worker
                    # after a closed-generation worker serves a request. Include ALL
                    # children now, including replacements missed by capture, then
                    # retire this census with another reload of the same closed file.
                    inventory = workers(binding)
                    barrier = list(binding["old_workers"])
                    for row in inventory:
                        identity = {"pid": row["pid"], "ticks": row["ticks"]}
                        if identity not in barrier:
                            barrier.append(identity)
                    binding.update(barrier_workers=barrier, phase="census")
                    (directory / "binding.json").write_text(json.dumps(binding, indent=2) + "\n")
                else:
                    time.sleep(0.2)
                # Refresh K1 before the additional bounded reload; do not stack
                # probe, reload and observation budgets on one old lease sample.
                continue
            if binding["phase"] == "census":
                command(["/usr/bin/systemctl", "reload", "nginx"])
                binding["phase"] = "fenced"
                (directory / "binding.json").write_text(json.dumps(binding, indent=2) + "\n")
                continue
            require(binding["phase"] == "fenced", "Nginx generation fence incomplete")
            remaining = remaining_workers(binding)
            if not remaining:
                return preflight(directory)
            time.sleep(0.2)
    except Exception:
        # Normal K1 remains running. Cancel every old API handler before the last
        # checked lease deadline if renewal/drain preflight fails; NEVER restart
        # while old open-config Nginx workers remain or renewal is unproven.
        stop_old_writer(last_good, emergency=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("capture", "close", "preflight", "wait"))
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "capture":
        capture(args.directory)
    elif args.command == "close":
        close(args.directory)
    elif args.command == "wait":
        print(json.dumps(wait_closed(args.directory), indent=2))
    else:
        print(json.dumps(preflight(args.directory), indent=2))


if __name__ == "__main__":
    main()
