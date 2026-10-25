#!/usr/bin/env python3
"""F40-only live, read-only post-resume observations. Never invokes a monitor/send."""

# Commands use fixed executables/arguments and explicitly selected owned fixture/artifact paths.
# ruff: noqa: S603

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path
from typing import Any, cast

from packages import require

SHOP = Path("/var/www/biotact")
TEAM = Path("/var/www/biotact-team-app/current")
STATE = Path("/var/lib/biotact-referral-maintenance/state.json")

SOURCE_SQL = """SELECT coalesce(json_object_agg(id::text, row), '{}'::json) FROM (
SELECT c.id, json_build_object(
'revision', r.content_revision, 'content_sha256', r.content_sha256,
'participants', r.participant_count,
'issued_at', extract(epoch from (r.confirmed_ack->'registry'->>'issued_at')::timestamptz),
'valid_until', extract(epoch from (r.confirmed_ack->'registry'->>'valid_until')::timestamptz),
'blocked', r.blocked_reason IS NOT NULL OR EXISTS(SELECT 1 FROM referral_k1_jobs j WHERE j.campaign_id=c.id AND j.state='blocked'),
'ack_matches', r.confirmed_at IS NOT NULL AND
(r.confirmed_ack->'registry'->>'content_revision')::bigint=r.content_revision AND
r.confirmed_ack->'registry'->>'content_sha256'=r.content_sha256 AND
r.confirmed_ack->'registry'->>'state'=c.state AND
(r.confirmed_ack->'registry'->>'participant_count')::bigint=r.participant_count
) AS row FROM campaigns c LEFT JOIN referral_campaign_registries r ON c.id=r.campaign_id
WHERE c.state='published' AND c.start_at<=now() AND now()<c.end_at) q;"""
REPLICA_SQL = """SELECT coalesce(json_object_agg(campaign_id::text, row), '{}'::json) FROM (
SELECT c.campaign_id,json_build_object(
'revision',c.content_revision,'content_sha256',c.content_sha256,
'participants',(SELECT count(*) FROM employee_referral_tokens t WHERE t.campaign_id=c.campaign_id AND t.in_current_registry),
'issued_at',extract(epoch from c.issued_at),'valid_until',extract(epoch from c.valid_until),
'receipt_matches',EXISTS(SELECT 1 FROM employee_referral_k1_receipts r
WHERE r.campaign_id=c.campaign_id AND r.content_revision=c.content_revision
AND r.content_sha256=c.content_sha256 AND r.issued_at=c.issued_at AND r.outcome IN ('applied','renewed'))
) AS row FROM employee_referral_campaigns c WHERE c.state='published' AND c.ended_at IS NULL
AND c.start_at<=now() AND now()<c.end_at) q;"""


def command(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=15, check=False)
    require(result.returncode == 0, "observation command failed: " + args[0])
    return result.stdout.strip()


def query(database: str, sql: str) -> dict[str, Any]:
    sql = (
        "BEGIN READ ONLY; SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s'; "
        + sql
        + " ROLLBACK;"
    )
    result = json.loads(
        command(
            [
                "runuser",
                "-u",
                "postgres",
                "--",
                "psql",
                "-X",
                "-qAt",
                "-v",
                "ON_ERROR_STOP=1",
                "-d",
                database,
                "-c",
                sql,
            ]
        )
    )

    require(isinstance(result, dict), "invalid metadata observation")
    return cast(dict[str, Any], result)


def identity(pid: int, venv: Path) -> dict[str, Any]:
    proc = Path("/proc") / str(pid)
    stat = (proc / "stat").read_text().rsplit(")", 1)[1].split()
    argv = (proc / "cmdline").read_bytes().split(b"\0")
    require(any(str(venv).encode() in arg for arg in argv), "consumer venv identity mismatch")
    require((proc / "cwd").resolve().is_relative_to(venv.parent.resolve()), "consumer cwd mismatch")
    return {
        "pid": pid,
        "start_ticks": int(stat[19]),
        "venv": str(venv.resolve()),
        "cwd": str((proc / "cwd").resolve()),
        "argv_sha256": hashlib.sha256((proc / "cmdline").read_bytes()).hexdigest(),
        "healthy": True,
    }


def continuity(receipt: dict[str, Any]) -> dict[str, Any]:
    """Bind readiness to live identities and configuration without exporting secrets."""
    require(
        receipt["expected_releases"]
        == {
            "shop": command(["git", "--no-optional-locks", "rev-parse", "HEAD"], SHOP),
            "team": str(TEAM.resolve()),
        },
        "release differs from approved operation",
    )
    consumers = {}
    rows = json.loads(command(["pm2", "jlist"]))
    for name, role, path in [
        ("api", "shop-api", SHOP / "packages/api/venv"),
        ("employee-referral-worker", "shop-worker", SHOP / "packages/api/venv"),
        ("bot", "shop-bot", SHOP / "packages/bot/venv"),
    ]:
        matches = [
            r for r in rows if r["name"] == name and r["pm2_env"].get("namespace") == "biotact"
        ]
        require(
            len(matches) == 1 and matches[0]["pm2_env"]["status"] == "online",
            "ambiguous/stopped Shop consumer",
        )
        consumers[role] = identity(matches[0]["pid"], path)
    for name in ("api", "worker", "bot"):
        pid = int(
            command(
                [
                    "systemctl",
                    "show",
                    "--property=MainPID",
                    "--value",
                    "biotact-team-" + name + ".service",
                ]
            )
        )
        require(pid > 0, "Team consumer stopped")
        consumers["team-" + name] = identity(pid, TEAM / "backend/.venv")
    timers = {}
    for timer in (
        "biotact-referral-watch.timer",
        "biotact-referral-recovery.timer",
        "biotact-team-monitor.timer",
    ):
        timers[timer] = command(["systemctl", "is-active", timer]) == "active"
    registrations = {}
    for role in ("shop", "portal"):
        registration = (STATE.parent / "participants" / role / "worker.json").read_bytes()
        registered = json.loads(registration)
        expected = consumers["shop-worker" if role == "shop" else "team-worker"]
        require(
            registered["pid"] == expected["pid"]
            and int(registered["ticks"]) == expected["start_ticks"]
            and registered["boot"] == Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "worker registration mismatch",
        )
        registrations[role] = hashlib.sha256(registration).hexdigest()
    unit_configuration = command(
        [
            "systemctl",
            "show",
            "--property=FragmentPath,DropInPaths,ExecStart,EnvironmentFiles,WorkingDirectory",
            "biotact-team-api.service",
            "biotact-team-worker.service",
            "biotact-team-bot.service",
            "biotact-referral-watch.timer",
            "biotact-referral-recovery.timer",
            "biotact-team-monitor.timer",
        ]
    )
    configuration: dict[str, Any] = {
        "systemd": hashlib.sha256(unit_configuration.encode()).hexdigest()
    }
    for path in (
        SHOP / "packages/api/.env",
        SHOP / "packages/bot/.env",
        SHOP / "ecosystem.config.js",
        Path("/etc/biotact-team/app.env"),
    ):
        stat = path.stat()
        configuration[str(path)] = [
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        ]
    return {
        "consumers": consumers,
        "registrations": registrations,
        "timers": timers,
        "configuration": configuration,
        "controller": hashlib.sha256(STATE.read_bytes()).hexdigest() if STATE.exists() else None,
        "releases": {
            "shop": command(["git", "--no-optional-locks", "rev-parse", "HEAD"], SHOP),
            "team": str(TEAM.resolve()),
        },
    }


def readiness(boundary: float) -> dict[str, bool]:
    """Run mandatory slow read-only health checks before the critical sample."""
    health = {}
    for port in (3001, 3002):
        command(
            [
                "curl",
                "-fsS",
                "--max-time",
                "2",
                "-o",
                "/dev/null",
                f"http://127.0.0.1:{port}/health",
            ]
        )
        health[str(port)] = True
    portal = json.loads(
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
                "--since",
                str(boundary),
            ],
            TEAM / "backend",
        )
    )
    require(
        portal.get("enabled") is True
        and portal.get("configuration_valid") is True
        and portal.get("ready") is True
        and portal.get("fresh") is True,
        "Team K1 renewal/readiness unavailable",
    )
    command(["bash", "ops/employee-referral-release.sh", "check"], SHOP)
    return health


def snapshot(receipt: dict[str, Any], boundary: float) -> dict[str, Any]:
    before = continuity(receipt)
    readiness_started = time.time()
    health = readiness(boundary)
    readiness_completed = time.time()
    started = time.time()
    after = continuity(receipt)
    require(before == after, "identity/configuration changed during readiness")
    gate = all(
        command(
            [
                "curl",
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
                "https://" + host + "/api/v1/orders",
            ]
        )
        == "423"
        for host in ("app.biotact.uz", "admin.biotact.uz")
    )
    # These three adjacent read-only queries carry no names, tokens, bodies or order rows.
    source = query("biotact_team_db", SOURCE_SQL)
    replica = query("biotact_db", REPLICA_SQL)
    source_after = query("biotact_team_db", SOURCE_SQL)
    return {
        "observed_at": started,
        "source": source,
        "replica": replica,
        "source_after": source_after,
        "consumers": after["consumers"],
        "health": {**health, **after["timers"]},
        "readiness": {
            "started_at": readiness_started,
            "completed_at": readiness_completed,
            "boundary": boundary,
            "before": before,
            "after": after,
        },
        "renewal_healthy": True,
        "gate_effective": gate,
        "controller_absent": not STATE.exists(),
        "releases": {
            "shop": command(["git", "--no-optional-locks", "rev-parse", "HEAD"], SHOP),
            "team": str(TEAM.resolve()),
        },
        "finished": receipt["finished"],
        "release_hold": receipt["release_hold"],
        "trust_passed": receipt["trust_passed"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "receipt", type=Path, help="operator record of completed finishes/hold/trust evidence"
    )
    parser.add_argument("boundary", type=float, help="post-finish-portal resume epoch")
    args = parser.parse_args()
    print(json.dumps(snapshot(json.loads(args.receipt.read_text()), args.boundary), indent=2))


if __name__ == "__main__":
    main()
