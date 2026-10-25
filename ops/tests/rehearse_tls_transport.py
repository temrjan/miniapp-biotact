"""Synthetic complete system-store replacement, fresh pip/uv public transports."""

# Commands use only fixed helper paths within the owned container.
# ruff: noqa: S603

import json
import subprocess
import sys
from pathlib import Path

work = Path("/work")
# Keep the fixture's old hashed directory as evidence; a bundle-only replacement
# is insufficient when OpenSSL also reads the hashed capath.
assert Path("/etc/ssl/certs/ca-certificates.crt").read_bytes() == (work / "fixed.pem").read_bytes()
for name, command in [
    (
        "pip-cli",
        [str(work / "shop-fixture-r2/bin/python"), "-B", "/helpers/transport.py", "pip-cli"],
    ),
    ("uv-cli", [sys.executable, "-B", "/helpers/transport.py", "uv-cli", "--uv", str(work / "uv")]),
]:
    result = subprocess.run(command, text=True, capture_output=True, timeout=90, check=False)
    (work / (name + ".r3.json")).write_text(result.stdout)
    print(name, result.returncode, result.stderr, flush=True)
    for host, row in json.loads(result.stdout).items():
        print(host, row.get("exit"), row["passed"], row.get("certificate_error"), flush=True)
    if result.returncode:
        raise SystemExit(result.returncode)
print("REAL_PIP_UV_CLI_TRANSPORT_PASSED", flush=True)
