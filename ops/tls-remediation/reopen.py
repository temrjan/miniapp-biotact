#!/usr/bin/env python3
"""F40 only: fresh complete K1/consumer proof, then restore only captured ingress."""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from gate import BASE_SHA, reopening
from observe import command, snapshot

from packages import require

SITE = Path("/etc/nginx/sites-enabled/biotact")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("previous", type=Path)
    parser.add_argument("boundary", type=float)
    args = parser.parse_args()
    binding = json.loads((args.directory / "binding.json").read_text())
    original = (args.directory / "original.conf").read_bytes()
    closed = (args.directory / "closed.conf").read_bytes()
    require(str(SITE.resolve()) == binding["site_resolved"], "site path changed")
    require(hashlib.sha256(original).hexdigest() == BASE_SHA, "original ingress corrupt")
    require(
        hashlib.sha256(closed).hexdigest() == binding["closed_sha256"], "closed ingress corrupt"
    )
    require(SITE.read_bytes() == closed, "current ingress changed; refuse removal")
    current = snapshot(json.loads(args.receipt.read_text()), args.boundary)
    (args.directory / "final-reopen-observation.json").write_text(
        json.dumps(current, indent=2) + "\n"
    )
    reopening(json.loads(args.previous.read_text()), current, args.boundary, time.time())
    require(SITE.read_bytes() == closed, "ingress changed during final checks")
    temporary = SITE.with_name(SITE.name + ".tls-open")
    require(not temporary.exists(), "unexpected temporary ingress")
    temporary.write_bytes(original)
    os.chmod(temporary, binding["mode"])
    os.chown(temporary, binding["uid"], binding["gid"])
    # Repeat age/lease/health predicates immediately before atomic restoration.
    reopening(json.loads(args.previous.read_text()), current, args.boundary, time.time())
    os.replace(temporary, SITE)
    try:
        command(["/usr/sbin/nginx", "-t"])
        command(["/usr/bin/systemctl", "reload", "nginx"])
    except Exception:
        # Retain the persistent gate after a failed/uncertain syntax/reload. A new
        # protected old-writer barrier is required before any later global pause.
        temporary.write_bytes(closed)
        os.chmod(temporary, binding["mode"])
        os.chown(temporary, binding["uid"], binding["gid"])
        os.replace(temporary, SITE)
        command(["/usr/sbin/nginx", "-t"])
        command(["/usr/bin/systemctl", "reload", "nginx"])
        raise
    require(SITE.read_bytes() == original, "original ingress not restored")
    print(
        "Captured ingress restored after fresh complete K1 proof; verify non-mutating reachability."
    )


if __name__ == "__main__":
    main()
