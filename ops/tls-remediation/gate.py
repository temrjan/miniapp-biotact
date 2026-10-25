#!/usr/bin/env python3
"""Exact Nginx 1.24 gate rendering and conservative K1/drain predicates.

No timeout/owner/controller operation removes this gate. This helper deliberately
has no automatic reopen command; the operator repeats live observations immediately
before the hash-bound original configuration restore in the private runbook.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from packages import require

BASE_SHA = "d281f2213d64a39d36b5731a8b62d6e96b0e8d5a96be9cc6f276a31a0280cb72"
MAP = """# TLS remediation: persistent new-order gate; removal requires fresh K1 proof.
map "$request_method:$uri" $tls_remediation_new_order {
    default 0;
    ~^POST:/api/v1/orders/?$ 1;
}
"""
LOCATION = "    location ^~ /api/v1/orders {\n"
REFUSAL = "        if ($tls_remediation_new_order) { return 423; }\n"


def render(original: bytes) -> bytes:
    require(hashlib.sha256(original).hexdigest() == BASE_SHA, "ingress changed; stop and reassess")
    text = original.decode()
    require(text.count(LOCATION) == 2, "expected exactly both Shop order locations")
    require(
        "server_name app.biotact.uz;" in text and "server_name admin.biotact.uz;" in text,
        "vhosts changed",
    )
    return (MAP + text.replace(LOCATION, LOCATION + REFUSAL)).encode()


def pre_pause(observation: dict[str, Any], now: float, stop_budget: float) -> None:
    """The old writer must end while the freshly sampled pre-pause lease is valid."""
    require(observation["gate_effective"] is True, "gate not effective on both vhosts")
    require(observation["renewal_healthy"] is True, "K1 renewal unhealthy")
    require(observation["old_nginx_workers"] == [], "open-configuration Nginx workers remain")
    require(now - observation["observed_at"] <= 5, "pre-pause observation stale")
    require(stop_budget > 0 and stop_budget <= 10, "stop budget outside the rehearsed bound")
    require(
        observation["minimum_valid_until"] - now > stop_budget, "insufficient fresh lease margin"
    )


def drained(observation: dict[str, Any], now: float) -> None:
    require(observation["gate_effective"] is True, "gate no longer effective")
    require(observation["old_writer_alive"] is False, "old API writer remains")
    require(observation["old_writer_db_sessions"] == 0, "old writer DB sessions remain")
    require(observation["old_writer_db_transactions"] == 0, "old writer DB transaction remains")
    require(now < observation["pre_pause_deadline"], "old-writer drain exceeded pre-pause deadline")


def reopening(
    previous: dict[str, Any], current: dict[str, Any], boundary: float, now: float
) -> None:
    """Validate tokenless rows from independent Team source and Shop replica queries."""
    require(0 <= now - current["observed_at"] <= 5, "final observations stale")
    ready = current["readiness"]
    require(
        boundary == ready["boundary"]
        and ready["started_at"] > previous["readiness"]["completed_at"]
        and boundary <= ready["started_at"] <= ready["completed_at"] <= current["observed_at"]
        and 0 <= now - ready["completed_at"] <= 5,
        "readiness stale or outside operation boundary",
    )
    require(ready["before"] == ready["after"], "readiness continuity broken")
    require(
        ready["after"]["consumers"] == current["consumers"]
        and ready["after"]["releases"] == current["releases"]
        and ready["after"]["controller"] is None
        and ready["after"]["timers"]
        and all(ready["after"]["timers"].values()),
        "readiness not bound to final identities/configuration",
    )
    require(boundary > 0 and boundary <= current["observed_at"], "invalid post-resume boundary")
    require(current["gate_effective"] is True, "gate not effective")
    require(current["finished"] == ["shop", "portal"], "both sequential finishes required")
    require(current["controller_absent"] is True, "controller operation remains")
    require(current["release_hold"] is True, "source/release hold missing")
    require(current["trust_passed"] is True, "repaired trust evidence missing")
    require(
        current["health"] and all(current["health"].values()),
        "intended consumer/timer health missing",
    )
    roles = {"shop-api", "shop-worker", "shop-bot", "team-api", "team-worker", "team-bot"}
    require(set(current["consumers"]) == roles, "consumer inventory incomplete")
    require(
        current["consumers"] == previous["consumers"],
        "consumer identity changed during renewal proof",
    )
    require(current["releases"] == previous["releases"], "release changed during observation")
    for role, identity in current["consumers"].items():
        require(
            identity["pid"] > 0
            and identity["start_ticks"] > 0
            and identity["venv"]
            and identity["healthy"] is True,
            "unverified consumer: " + role,
        )
    require(current["renewal_healthy"] is True, "continuing Team renewal not healthy")
    source = current["source"]
    require(
        set(source)
        == set(current["source_after"])
        == set(current["replica"])
        == set(previous["source"]),
        "active campaign set partial/changed",
    )
    for campaign, expected in source.items():
        after, replica, old = (
            current["source_after"][campaign],
            current["replica"][campaign],
            previous["source"][campaign],
        )
        for key in ("revision", "content_sha256", "participants"):
            require(
                expected[key] == after[key] == replica[key] == old[key],
                "campaign content changed/mismatched",
            )
        require(
            expected["blocked"] is False and after["blocked"] is False, "campaign renewal blocked"
        )
        require(
            expected["ack_matches"] is True
            and after["ack_matches"] is True
            and replica["receipt_matches"] is True,
            "post-resume K1 ACK/receipt missing",
        )
        require(expected["issued_at"] > old["issued_at"], "continuing renewal not observed")
        require(expected["issued_at"] >= boundary, "K1 issuance predates post-resume boundary")
        require(
            expected["issued_at"] == replica["issued_at"] == after["issued_at"],
            "K1 issuance changed during final snapshot; repeat",
        )
        require(
            expected["valid_until"] == replica["valid_until"] == after["valid_until"],
            "lease mismatch",
        )
        require(
            expected["issued_at"] <= now < expected["valid_until"] - 10,
            "fresh K1 margin insufficient",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("render")
    p.add_argument("original", type=Path)
    p.add_argument("closed", type=Path)
    p = sub.add_parser("reopen-check")
    p.add_argument("previous", type=Path)
    p.add_argument("current", type=Path)
    p.add_argument("boundary", type=float)
    args = parser.parse_args()
    if args.command == "render":
        require(not args.closed.exists(), "refuse replacing an existing candidate")
        args.closed.write_bytes(render(args.original.read_bytes()))
        print(hashlib.sha256(args.closed.read_bytes()).hexdigest())
    else:
        reopening(
            json.loads(args.previous.read_text()),
            json.loads(args.current.read_text()),
            args.boundary,
            time.time(),
        )
        print(
            "Reopen predicates passed for this observation only; repeat live checks before restore."
        )


if __name__ == "__main__":
    main()
