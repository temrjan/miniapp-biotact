#!/usr/bin/env python3
"""Fresh public TLS handshakes and real package-manager transport, without installs."""

# Commands use fixed executables/arguments and explicitly selected owned fixture/artifact paths.
# ruff: noqa: S603

import argparse
import hashlib
import importlib
import json
import os
import re
import smtplib
import socket
import ssl
import subprocess
import sys
from pathlib import Path
from typing import Any

INCIDENT = [
    "dv-g3b-emudhra.we-have-a-problem.com",
    "dv-g3b-noct-emudhra.we-have-a-problem.com",
    "dv-g3b-aaa-emudhra.we-have-a-problem.com",
    "dv-g3b-aaa-noct-emudhra.we-have-a-problem.com",
]
CONTROLS = [
    "pypi.org",
    "files.pythonhosted.org",
    "api.telegram.org",
    "app.biotact.uz",
    "mesh.multicard.uz",
    "team.biotact.uz",
]


def matrix() -> dict[str, Any]:
    results: dict[str, Any] = {}
    for mode in ("default", "certifi", "pip._vendor.certifi"):
        try:
            bundle = None if mode == "default" else importlib.import_module(mode).where()
        except ImportError:
            results[mode] = {"present": False}
            continue
        context = ssl.create_default_context(cafile=bundle)
        affected = [
            str(c["subject"])
            for c in context.get_ca_certs()
            if "emSign ECC Root CA - G3" in str(c["subject"])
        ]
        entry: dict[str, Any] = {
            "bundle": bundle,
            "sha256": hashlib.sha256(Path(bundle).read_bytes()).hexdigest() if bundle else None,
            "affected": affected,
            "tls": {},
        }
        for host in INCIDENT + CONTROLS:
            try:
                with (
                    socket.create_connection((host, 443), timeout=5) as connection,
                    context.wrap_socket(connection, server_hostname=host),
                ):
                    entry["tls"][host] = "accepted"
            except ssl.SSLCertVerificationError:
                entry["tls"][host] = "certificate_rejected"
            except OSError as exc:
                entry["tls"][host] = "inconclusive:" + type(exc).__name__
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=5) as smtp:
                smtp.ehlo()
                smtp.starttls(context=context)
                entry["smtp"] = "accepted"
        except (OSError, smtplib.SMTPException) as exc:
            entry["smtp"] = "inconclusive:" + type(exc).__name__
        results[mode] = entry
    return results


def cli(uv: Path | None) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for host in ["pypi.org", *INCIDENT]:
        if uv is None:
            args = [
                sys.executable,
                "-I",
                "-B",
                "-m",
                "pip",
                "--isolated",
                "--disable-pip-version-check",
                "index",
                "versions",
                "certifi",
                "--no-cache-dir",
                "--retries",
                "0",
                "--timeout",
                "5",
                "--index-url",
                "https://" + host + "/simple",
            ]
            data = None
        else:
            args = [
                str(uv),
                "--no-config",
                "--no-cache",
                "pip",
                "compile",
                "-",
                "--no-deps",
                "--index-url",
                "https://" + host + "/simple",
            ]
            data = "certifi==2026.10.19\n"
        try:
            direct_env = {
                k: v
                for k, v in os.environ.items()
                if not k.upper().endswith("_PROXY")
                and k
                not in {
                    "SSL_CERT_FILE",
                    "SSL_CERT_DIR",
                    "REQUESTS_CA_BUNDLE",
                    "CURL_CA_BUNDLE",
                    "PIP_CERT",
                }
                and not k.startswith("UV_")
            }
            direct_env.update(UV_HTTP_TIMEOUT="5", UV_HTTP_RETRIES="0", UV_PYTHON_DOWNLOADS="never")
            run = subprocess.run(
                args,
                input=data,
                text=True,
                capture_output=True,
                timeout=45,
                check=False,
                env=direct_env,
            )
            text = run.stdout + run.stderr
            rejection = bool(
                re.search(
                    r"CERTIFICATE_VERIFY_FAILED|certificate verify failed|invalid peer certificate|invalid certificate|UnknownIssuer",
                    text,
                    re.I,
                )
            )
            passed = (
                run.returncode == 0 if host == "pypi.org" else run.returncode != 0 and rejection
            )
            results[host] = {
                "command": args,
                "exit": run.returncode,
                "certificate_error": rejection,
                "passed": passed,
                "stdout": run.stdout,
                "stderr": run.stderr,
            }
        except subprocess.TimeoutExpired:
            results[host] = {
                "command": args,
                "passed": False,
                "error": "transport timeout; inconclusive",
            }
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("matrix", "pip-cli", "uv-cli"))
    parser.add_argument("--uv", type=Path)
    args = parser.parse_args()
    result = matrix() if args.mode == "matrix" else cli(args.uv if args.mode == "uv-cli" else None)
    print(json.dumps(result, indent=2))
    if args.mode == "matrix":
        good = all(
            not row.get("affected")
            and all(row["tls"][host] == "certificate_rejected" for host in INCIDENT)
            and all(row["tls"][host] == "accepted" for host in CONTROLS)
            and row["smtp"] == "accepted"
            for row in result.values()
            if row.get("present") is not False
        )
    else:
        good = all(row["passed"] for row in result.values())
    sys.exit(0 if good else 1)


if __name__ == "__main__":
    main()
