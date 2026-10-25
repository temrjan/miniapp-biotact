#!/usr/bin/env python3
"""F40-only bounded pre-pause PM2 API drain while normal K1 renewal runs."""

# Commands use fixed executables/arguments and explicitly selected owned fixture/artifact paths.
# ruff: noqa: S607

import argparse
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from gate import drained, pre_pause
from observe import command, identity, query

from packages import require

# SQL fragments contain only validated TCP port/PID integers and parsed DB timestamps.
# ruff: noqa: S608


def connections(pid: int) -> list[int]:
    proc = Path("/proc") / str(pid)
    sockets = {
        p.readlink().as_posix()[8:-1]
        for p in (proc / "fd").iterdir()
        if p.is_symlink() and p.readlink().as_posix().startswith("socket:[")
    }
    ports = []
    for filename in ("tcp", "tcp6"):
        for row in (proc / "net" / filename).read_text().splitlines()[1:]:
            fields = row.split()
            if fields[9] in sockets and int(fields[2].rsplit(":", 1)[1], 16) == 5432:
                ports.append(int(fields[1].rsplit(":", 1)[1], 16))
    return ports


def stop_old_writer(observation: dict[str, Any], *, emergency: bool = False) -> None:
    if emergency:
        require(
            observation["minimum_valid_until"] - time.time() > 10,
            "emergency stop lease margin exhausted; immediate operator escalation",
        )
    else:
        pre_pause(observation, time.time(), 10)
    rows = json.loads(command(["pm2", "jlist"]))
    api = [r for r in rows if r["name"] == "api" and r["pm2_env"].get("namespace") == "biotact"]
    require(len(api) == 1 and api[0]["pm2_env"]["status"] == "online", "API inventory changed")
    pid = api[0]["pid"]
    metadata = api[0]["pm2_env"]
    require(
        metadata.get("exec_mode") == "fork_mode"
        and metadata.get("kill_timeout") is None
        and metadata.get("treekill") is True,
        "PM2 stop contract changed",
    )
    require(
        not Path(f"/proc/{pid}/task/{pid}/children").read_text().strip(),
        "API child writers found; refuse runtime route",
    )
    parent = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
    daemon_env = Path(f"/proc/{parent}/environ").read_bytes().split(b"\0")
    require(
        not any(v.startswith(b"PM2_KILL_TIMEOUT=") for v in daemon_env),
        "PM2 daemon kill timeout override found",
    )
    old = identity(pid, Path("/var/www/biotact/packages/api/venv"))
    ports = connections(pid)
    if not emergency:
        require(bool(ports), "old API TCP database binding unavailable; refuse this runtime route")
    # Capture only server process identities, not SQL text or customer rows.
    port_list = ",".join(str(p) for p in ports) or "-1"
    backend_data = query(
        "biotact_db",
        "SELECT json_build_object('rows',coalesce(json_agg(json_build_object('pid',pid,'start',backend_start)),'[]')) FROM pg_stat_activity WHERE datname='biotact_db' AND client_port IN ("
        + port_list
        + ");",
    )
    backends = backend_data["rows"]
    if emergency:
        require(
            observation["minimum_valid_until"] - time.time() > 10,
            "emergency stop lease margin exhausted; immediate operator escalation",
        )
    else:
        pre_pause(observation, time.time(), 10)
    deadline = min(time.time() + 10, observation["minimum_valid_until"])
    require(deadline - time.time() > 2, "lease margin spent before stop")
    # PM2's observed default is 1600ms, with SIGKILL escalation. No altered timeout.
    result = subprocess.run(
        ["pm2", "stop", "api", "--namespace", "biotact"],
        text=True,
        capture_output=True,
        timeout=max(0.1, deadline - time.time()),
        check=False,
    )
    require(result.returncode == 0, "PM2 stop failed; keep gate, protect writer and escalate")
    require(
        bool(ports),
        "emergency API stop completed, but DB binding proof is unavailable; do not begin maintenance",
    )
    while time.time() < deadline:
        try:
            same = (
                identity(pid, Path("/var/www/biotact/packages/api/venv"))["start_ticks"]
                == old["start_ticks"]
            )
        except FileNotFoundError:
            same = False
        clauses = [
            "(pid="
            + str(int(r["pid"]))
            + " AND backend_start='"
            + datetime.fromisoformat(r["start"]).isoformat()
            + "'::timestamptz)"
            for r in backends
        ]
        active = query(
            "biotact_db",
            "SELECT json_build_object('sessions',count(*),'transactions',count(xact_start)) FROM pg_stat_activity WHERE "
            + "(datname='biotact_db' AND client_port IN ("
            + port_list
            + ")) OR "
            + (" OR ".join(clauses) or "false")
            + ";",
        )
        if not same and active["sessions"] == 0:
            final = {
                "gate_effective": True,
                "old_writer_alive": False,
                "old_writer_db_sessions": 0,
                "old_writer_db_transactions": 0,
                "pre_pause_deadline": observation["minimum_valid_until"],
            }
            drained(final, time.time())
            print(
                json.dumps(
                    {
                        "old_writer": old,
                        "backend_count": len(backends),
                        "completed_at": time.time(),
                        "proof": final,
                    },
                    indent=2,
                )
            )
            return
        time.sleep(0.05)
    raise ValueError("bounded drain failed; never begin maintenance or reopen")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("preflight", type=Path)
    args = parser.parse_args()
    stop_old_writer(json.loads(args.preflight.read_text()))


if __name__ == "__main__":
    main()
