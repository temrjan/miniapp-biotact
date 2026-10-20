"""Actual CLI subprocess signal/restart and named release boundary substitutes."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import textwrap
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from src.employee_referrals.delivery_models import K2Delivery, NoticeDelivery
from src.employee_referrals.delivery_store import now
from tests.test_employee_referral_delivery import jobs
from tests.test_employee_referral_delivery import secret as secret
from tests.test_employee_referral_order_capture import checkout as checkout
from tests.test_employee_referral_returns import paid_order

BOUNDARY = """
import asyncio,json,os
from pathlib import Path
import httpx
async def boundary(request):
    marker=Path(os.environ['G6A_BOUNDARY_MARKER'])
    if os.environ.get('G6A_BOUNDARY_STALL')=='true':
        marker.write_text('network_started')
        await asyncio.Event().wait()
    if request.url.host=='api.telegram.org':
        value={'ok':True,'result':{'message_id':42}}
        hdr={'content-type':'application/json'}
    else:
        source=json.loads(request.content)
        import hashlib
        value={'schema_version':1,'receipt':{k:source[k] for k in ('event_id','source','order_id','order_version','source_position')},'replayed':False,'projection':{'order_version':source['order_version'],'accounting_state':'confirmed','has_conflict':False}}
        value['receipt'].update(body_sha256=hashlib.sha256(request.content).hexdigest(),outcome='applied',received_at=source['source_recorded_at'])
        hdr={'content-type':'application/json','cache-control':'no-store'}
    return httpx.Response(200,stream=httpx.ByteStream(json.dumps(value).encode()),headers=hdr)
httpx.AsyncHTTPTransport=lambda **_kwargs:httpx.MockTransport(boundary)
"""


async def wait_until(predicate):
    async with asyncio.timeout(12):
        while not await predicate():  # noqa: ASYNC110 - poll external PG/process state
            await asyncio.sleep(0.02)


async def test_actual_worker_sigterm_sigkill_recovery_and_health(checkout, tmp_path):
    h = checkout
    await paid_order(h)
    boundary_dir = tmp_path / "external"
    boundary_dir.mkdir()
    (boundary_dir / "sitecustomize.py").write_text(BOUNDARY)
    root = Path.cwd()
    marker = tmp_path / "marker"
    env = dict(
        os.environ,
        PYTHONPATH=str(boundary_dir) + os.pathsep + str(root),
        EMPLOYEE_REFERRAL_K2_SENDER_ENABLED="true",
        EMPLOYEE_REFERRAL_K2_PORTAL_ORIGIN="https://portal.example.test",
        EMPLOYEE_REFERRAL_K2_KEY="00" * 32,
        EMPLOYEE_REFERRAL_NOTICES_ENABLED="true",
        TELEGRAM_NOTIFICATION_BOT_TOKEN="12345:synthetic_g6a",
        TELEGRAM_CHAT_ID="-12345",
        G6A_BOUNDARY_MARKER=str(marker),
    )
    processes = []

    def start(stall, **changes):
        process = subprocess.Popen(
            [sys.executable, "-m", "src.employee_referrals.worker", "run"],
            env=dict(env, G6A_BOUNDARY_STALL="true" if stall else "false", **changes),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        processes.append(process)
        return process

    async def started():
        leased = [
            any(j.state == "leased" for j in await jobs(h, model))
            for model in (K2Delivery, NoticeDelivery)
        ]
        return marker.exists() and all(leased)

    async def completed():
        return all(j.state == "acknowledged" for j in await jobs(h, K2Delivery)) and all(
            j.state == "sent" for j in await jobs(h, NoticeDelivery)
        )

    async def expire():
        async with h.sessions.begin() as session:
            for model in (K2Delivery, NoticeDelivery):
                for job in await session.scalars(select(model).where(model.state == "leased")):
                    job.leased_until = await now(session) - timedelta(seconds=1)

    try:
        first = start(True)
        await wait_until(started)
        first.send_signal(signal.SIGTERM)
        stdout, _ = await asyncio.to_thread(first.communicate, timeout=10)
        assert first.returncode == 0
        saved = await jobs(h, K2Delivery)
        assert saved[0].attempt_count == 1
        assert saved[0].state == "leased"
        await expire()
        second = start(False)
        await wait_until(completed)
        second.send_signal(signal.SIGTERM)
        stdout2, _ = await asyncio.to_thread(second.communicate, timeout=10)
        assert second.returncode == 0
        saved = await jobs(h, K2Delivery)
        assert saved[0].attempt_count == 2
        assert saved[0].generation == 2
        assert "Referral worker cycle lane=k2 status=active" in stdout2
        await paid_order(h)
        marker.unlink()
        third = start(True)
        await wait_until(started)
        third.kill()
        await asyncio.to_thread(third.communicate, timeout=10)
        assert third.returncode == -signal.SIGKILL
        assert (await jobs(h, K2Delivery))[1].state == "leased"
        await expire()
        fourth = start(False)
        await wait_until(completed)
        fourth.send_signal(signal.SIGTERM)
        stdout4, _ = await asyncio.to_thread(fourth.communicate, timeout=10)
        assert fourth.returncode == 0
        assert (await jobs(h, K2Delivery))[1].attempt_count == 2
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "src.employee_referrals.worker", "health"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        value = json.loads(result.stdout)
        assert value["k2"]["counts"]["completed"] == 2
        assert value["notice"]["counts"]["completed"] == 2
        assert "synthetic_g6a" not in stdout + stdout2 + stdout4 + result.stdout + result.stderr
        await paid_order(h)
        fifth = start(False, EMPLOYEE_REFERRAL_K2_PORTAL_ORIGIN="http://invalid.example")

        async def notice_only():
            return (await jobs(h, NoticeDelivery))[2].state == "sent"

        await wait_until(notice_only)
        fifth.send_signal(signal.SIGTERM)
        stdout5, _ = await asyncio.to_thread(fifth.communicate, timeout=10)
        assert fifth.returncode == 0
        pending = (await jobs(h, K2Delivery))[2]
        assert pending.state == "pending"
        assert pending.attempt_count == 0
        assert "lane=k2 status=configuration" in stdout5
        sixth = start(
            False,
            EMPLOYEE_REFERRAL_K2_SENDER_ENABLED="false",
            EMPLOYEE_REFERRAL_NOTICES_ENABLED="false",
        )
        first_cycle = await asyncio.wait_for(asyncio.to_thread(sixth.stdout.readline), timeout=5)
        assert "status=disabled" in first_cycle
        sixth.send_signal(signal.SIGTERM)
        stdout6, _ = await asyncio.to_thread(sixth.communicate, timeout=10)
        stdout6 = first_cycle + stdout6
        assert sixth.returncode == 0
        assert "status=disabled" in stdout6
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                await asyncio.to_thread(process.wait, timeout=10)


def test_release_first_start_restart_failed_health_and_compatible_rollback(tmp_path):
    root = Path.cwd().parents[1]
    fake = tmp_path / "release"
    fake.mkdir()
    (fake / "ops").mkdir()
    for name in ("employee-referral-release.sh", "employee-referral-runtime.py"):
        (fake / "ops" / name).write_bytes((root / "ops" / name).read_bytes())
    python = fake / "packages/api/venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text(
        '#!/usr/bin/env bash\nif [ "${1:-}" = "-m" ]; then echo \'{"result":"health"}\'; exit "${FAKE_HEALTH_EXIT:-0}"; fi\nif [ "${1:-}" = "ops/employee-referral-runtime.py" ]; then exec "$G6A_REAL_PYTHON" "$G6A_RUNTIME_SOURCE"; fi\nexec "$G6A_REAL_PYTHON" "$@"\n'
    )
    python.chmod(0o755)
    bin_dir = fake / "bin"
    bin_dir.mkdir()
    trace = fake / "trace"
    pm2 = bin_dir / "pm2"
    pm2.write_text("""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$G6A_PM2_TRACE"
case "$1" in
 pid) echo 123;;
 jlist) printf '%s\\n' "$G6A_PM2_RUNTIME";;
esac
""")
    pm2.chmod(0o755)
    git = bin_dir / "git"
    git.write_text(
        '#!/usr/bin/env bash\nif [ "$1" = "cat-file" ]; then exit "${FAKE_COMPAT_EXIT:-0}"; fi\nexit 0\n'
    )
    git.chmod(0o755)
    runtime = [
        {
            "name": "employee-referral-worker",
            "pid": 123,
            "pm2_env": {
                "namespace": "biotact",
                "status": "online",
                "pm_cwd": "/var/www/biotact/packages/api",
                "pm_exec_path": "/var/www/biotact/packages/api/venv/bin/python",
                "exec_interpreter": "none",
                "args": ["-m", "src.employee_referrals.worker", "run"],
                "restart_time": 2,
            },
        },
        {"name": "ref-bot", "private": "PRIVATE_PM2_CANARY"},
    ]
    env = dict(
        os.environ,
        PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
        G6A_REAL_PYTHON=sys.executable,
        G6A_RUNTIME_SOURCE=str(root / "ops/employee-referral-runtime.py"),
        G6A_PM2_TRACE=str(trace),
        G6A_PM2_RUNTIME=json.dumps(runtime),
        G6A_FAKE_RELEASE=str(fake),
    )
    # cd is an external filesystem boundary; release decision logic stays unchanged.
    shell = 'cd() { if [ "${1:-}" = "/var/www/biotact" ]; then builtin cd "$G6A_FAKE_RELEASE"; else builtin cd "$@"; fi; }; export -f cd; bash ops/employee-referral-release.sh "$@"'

    def call(*args, **changes):
        return subprocess.run(  # noqa: S603 - fixed shell and owned boundary stubs
            ["/bin/bash", "-c", shell, "release", *args],
            cwd=fake,
            env=dict(env, **changes),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    for _ in range(2):
        result = call("start")
        assert result.returncode == 0, result.stdout + result.stderr
    lines = trace.read_text().splitlines()
    assert (
        lines.count(
            "startOrRestart ecosystem.config.js --only employee-referral-worker --update-env"
        )
        == 2
    )
    assert lines.count("save") == 2
    result = call("start", FAKE_HEALTH_EXIT="1")
    assert result.returncode != 0
    assert trace.read_text().splitlines().count("save") == 2
    result = call("rollback", "previous", "true")
    assert result.returncode == 0
    result = call("rollback", "previous", "false", FAKE_COMPAT_EXIT="1")
    assert result.returncode == 0
    assert "delete employee-referral-worker --namespace biotact" in trace.read_text()
    before = trace.read_text()
    result = call("rollback", "previous", "true", FAKE_COMPAT_EXIT="1")
    assert result.returncode != 0
    tail = trace.read_text()[len(before) :]
    assert "stop api --namespace biotact" in tail
    assert "stop employee-referral-worker --namespace biotact" in tail
    assert "restart" not in tail
    assert "PRIVATE_PM2_CANARY" not in result.stdout + result.stderr
    assert " all" not in trace.read_text()

    # Execute the actual workflow's SSH payload locally with external boundaries substituted.
    for directory in ("packages/frontend", "admin-panel-v2", "packages/bot/venv/bin"):
        (fake / directory).mkdir(parents=True, exist_ok=True)
    for package in ("api", "bot"):
        (fake / f"packages/{package}/venv/bin/activate").write_text("deactivate() { :; }\n")
    for owner in ("packages/frontend", "admin-panel-v2"):
        dist = fake / owner / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("baseline")
    baseline = "1" * 40
    target = "2" * 40
    revision = fake / "revision"
    env.update(
        G6A_REVISION=str(revision),
        G6A_TARGET=target,
        G6A_ASSET_HELPER=str(root / "ops/shop-release-assets.py"),
    )
    git.write_text("""#!/usr/bin/env bash
printf 'git %s\\n' "$*" >> "$G6A_PM2_TRACE"
case "$1" in
 rev-parse) cat "$G6A_REVISION";;
 reset) printf '%s\\n' "$3" > "$G6A_REVISION";;
 show) cat "$G6A_ASSET_HELPER";;
 cat-file) exit "${FAKE_COMPAT_EXIT:-0}";;
 fetch) exit "${FAKE_FETCH_EXIT:-0}";;
esac
""")
    for name in ("npm", "pip", "alembic", "sleep"):
        stub = bin_dir / name
        stub.write_text(f'#!/usr/bin/env bash\nexit "${{FAKE_{name.upper()}_EXIT:-0}}"\n')
        stub.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text(
        '#!/usr/bin/env bash\nif [ "$(cat "$G6A_REVISION")" = "$G6A_TARGET" ]; then exit "${FAKE_CURL_EXIT:-0}"; fi\n'
    )
    curl.chmod(0o755)
    workflow = (root / ".github/workflows/deploy.yml").read_text()
    payload = textwrap.dedent(workflow.split("<< 'SCRIPT'\n", 1)[1].split("          SCRIPT", 1)[0])
    # The root-installed guard is an external operator prerequisite. Model its
    # fixed path locally; the new workflow ordering has separate real Bash tests.
    guard = fake / "guard"
    guard.write_text(
        '#!/usr/bin/env bash\nprintf "guard %s\\n" "$*" >> "$G6A_PM2_TRACE"\nexit "${FAKE_GUARD_EXIT:-0}"\n'
    )
    guard.chmod(0o755)
    sudo = bin_dir / "sudo"
    sudo.write_text('#!/usr/bin/env bash\nshift 2\nexec "$@"\n')
    sudo.chmod(0o755)
    payload = payload.replace("/usr/local/sbin/biotact-referral-maintenance", str(guard))
    payload = payload.replace("${{ github.event.workflow_run.head_sha }}", target)
    prefix = shell.split("; bash ops/", 1)[0] + "; "

    def deploy(**changes):
        revision.write_text(baseline + "\n")
        return subprocess.run(  # noqa: S603 - actual payload, own release directory and stubs
            ["/bin/bash", "-c", prefix + payload],
            cwd=fake,
            env=dict(env, **changes),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    result = deploy()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Deploy complete" in result.stdout
    before = trace.read_text()
    result = deploy(FAKE_NPM_EXIT="1", FAKE_COMPAT_EXIT="1")
    assert result.returncode != 0
    assert "delete employee-referral-worker --namespace biotact" in trace.read_text()[len(before) :]
    assert "Deploy complete" not in result.stdout
    before = trace.read_text()
    result = deploy(FAKE_ALEMBIC_EXIT="1", FAKE_COMPAT_EXIT="1")
    assert result.returncode != 0
    tail = trace.read_text()[len(before) :]
    assert "stop api --namespace biotact" in tail
    assert "restart api" not in tail
    assert "Deploy complete" not in result.stdout
    before = trace.read_text()
    result = deploy(FAKE_CURL_EXIT="1")
    assert result.returncode != 0
    assert f"git reset --hard {baseline}" in trace.read_text()[len(before) :]
    assert "Rollback complete" in result.stdout
    assert "Deploy complete" not in result.stdout
    (fake / "ops/employee-referral-release.sh").unlink()
    result = deploy(FAKE_FETCH_EXIT="1")
    assert result.returncode != 0
    assert "Deploy complete" not in result.stdout
    assert "PRIVATE_PM2_CANARY" not in result.stdout + result.stderr
    assert " all" not in trace.read_text()


def test_actual_runtime_verifier_safe_refusal_and_string_args(monkeypatch, capsys):
    import importlib.util
    import io

    source = Path.cwd().parents[1] / "ops/employee-referral-runtime.py"
    spec = importlib.util.spec_from_file_location("g6a_runtime_verifier", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    row = {
        "name": "employee-referral-worker",
        "pid": 23,
        "pm2_env": {
            "namespace": "biotact",
            "status": "online",
            "pm_cwd": "/var/www/biotact/packages/api",
            "pm_exec_path": "/var/www/biotact/packages/api/venv/bin/python",
            "exec_interpreter": "none",
            "args": "-m src.employee_referrals.worker run",
            "restart_time": 0,
            "secret": "PRIVATE_RUNTIME_CANARY",
        },
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps([row])))
    assert module.main() == 0
    row["pid"] = 0
    for value in (json.dumps([row]), "[]", "PRIVATE_RUNTIME_CANARY"):
        monkeypatch.setattr(sys, "stdin", io.StringIO(value))
        assert module.main() == 1
    assert "PRIVATE_RUNTIME_CANARY" not in capsys.readouterr().out
