"""Actual PM2 6.0.14 hard-stop, stuck request and PostgreSQL transaction fixture."""

# Commands use fixed executables/arguments and explicitly selected owned fixture/artifact paths.
# ruff: noqa: S603, S607

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / ".venv/tls-f30-work"
ART = Path(sys.argv[1])
PM2 = WORK / "pm2-fixture/node_modules/pm2/bin/pm2"
PYTHON = Path("/home/temrjan/Dev/projects/biotact-team-app/backend/.venv/bin/python")
name = "tls-f30-postgres-r1"
env = dict(os.environ, PM2_HOME=str(WORK / "pm2-home"), PM2_DISABLE_INTERACTION="true")


def run(args, **kwargs):
    return subprocess.run(
        [str(x) for x in args], text=True, capture_output=True, timeout=20, check=True, **kwargs
    ).stdout.strip()


def psql(sql):
    return run(
        [
            "podman",
            "exec",
            name,
            "psql",
            "-U",
            "tls_fixture",
            "-d",
            "tls_fixture",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ]
    )


try:
    port = int(run(["podman", "port", name, "5432"]).rsplit(":", 1)[1])
    for _ in range(50):
        try:
            psql("SELECT 1")
            break
        except subprocess.CalledProcessError:
            time.sleep(0.1)
    script = WORK / "stuck-api.py"
    script.write_text(
        """import asyncio,asyncpg,signal,os
signal.signal(signal.SIGINT,signal.SIG_IGN)
signal.signal(signal.SIGTERM,signal.SIG_IGN)
async def main():
 c=await asyncpg.connect(host='127.0.0.1',port="""
        + str(port)
        + """,user='tls_fixture',password='tls_fixture',database='tls_fixture',server_settings={'application_name':'tls-f30-old-writer'})
 await c.execute('BEGIN')
 await c.fetchval('SELECT 1')
 print('OLD_WRITER_READY',flush=True)
 while True:await asyncio.sleep(1)
asyncio.run(main())
"""
    )
    print(
        run(
            [
                "node",
                PM2,
                "start",
                script,
                "--interpreter",
                PYTHON,
                "--name",
                "api",
                "--namespace",
                "biotact",
            ],
            env=env,
        ),
        flush=True,
    )
    for _ in range(50):
        if (
            psql(
                "SELECT count(*) FROM pg_stat_activity WHERE application_name='tls-f30-old-writer' AND xact_start IS NOT NULL"
            )
            == "1"
        ):
            break
        time.sleep(0.1)
    assert (
        psql(
            "SELECT count(*) FROM pg_stat_activity WHERE application_name='tls-f30-old-writer' AND xact_start IS NOT NULL"
        )
        == "1"
    )
    rows = json.loads(run(["node", PM2, "jlist"], env=env))
    old = next(r["pid"] for r in rows if r["name"] == "api")
    started = time.monotonic()
    print(run(["node", PM2, "stop", "api", "--namespace", "biotact"], env=env), flush=True)
    duration = time.monotonic() - started
    assert duration < 10, duration
    assert not Path(f"/proc/{old}").exists(), "exact old writer remains alive"
    assert (
        psql("SELECT count(*) FROM pg_stat_activity WHERE application_name='tls-f30-old-writer'")
        == "0"
    )
    print(
        json.dumps(
            {
                "old_pid": old,
                "elapsed_seconds": duration,
                "pm2_default_kill_timeout_ms": 1600,
                "old_writer_gone": True,
                "db_sessions": 0,
                "db_transactions": 0,
                "scope": "real PM2 stop with ignored SIGINT/SIGTERM and real synthetic PostgreSQL transaction; no production writer",
            }
        ),
        flush=True,
    )
    # Same named consumer restarts once; no new named consumer or duplicate polling.
    run(["node", PM2, "restart", "api", "--namespace", "biotact"], env=env)
    rows = json.loads(run(["node", PM2, "jlist"], env=env))
    active = [r for r in rows if r["name"] == "api" and r["pm2_env"]["namespace"] == "biotact"]
    assert len(active) == 1
    assert active[0]["pid"] != old
    print("NAMED_RESTART_SINGLE_NEW_PID_PASSED", flush=True)
finally:
    subprocess.run(
        ["node", str(PM2), "kill"], env=env, capture_output=True, timeout=20, check=False
    )
    subprocess.run(
        ["podman", "stop", "--time", "2", name], capture_output=True, timeout=20, check=False
    )
