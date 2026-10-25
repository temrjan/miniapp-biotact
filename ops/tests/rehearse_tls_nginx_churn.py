"""Real Nginx1.24 respawn/buffered-request regression through close/wait/preflight.

K1, systemctl and HTTPS-to-local-HTTP are controlled external boundaries. The
master, workers, reloads, 100 Continue and upstream observations are real.
"""

# Controlled local fixture commands; no production service operation.
# ruff: noqa: S603, S607

import concurrent.futures
import contextlib
import hashlib
import http.client
import http.server
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
ART = Path(sys.argv[1])
LABEL = sys.argv[2]
sys.path.insert(0, sys.argv[3] if len(sys.argv) > 3 else str(ROOT / "ops/tls-remediation"))
import close  # noqa: E402
import drain  # noqa: E402
import gate  # noqa: E402

WORK = ROOT / ".venv/tls-f30-correction-r2" / LABEL
WORK.mkdir()
NAME = "tls-f30-correction-nginx-" + LABEL
IMAGE = "249f59e1dec7f7eacbeba4bb9215b8000e4bdbb672af523b3dacc89915b026ae"
requests = []


class Upstream(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.reply()

    def do_GET(self):
        self.reply()

    def reply(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        requests.append(
            (self.command, self.path, body.decode(), self.headers.get("X-TLS-Buffered"))
        )
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"fixture")

    def log_message(self, *_args):
        return


upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
server_thread = threading.Thread(target=upstream.serve_forever)
server_thread.start()
with socket.socket() as chosen:
    chosen.bind(("127.0.0.1", 0))
    port = chosen.getsockname()[1]

base = json.loads((ART / "f30-nginx-input.r1.json").read_text())["files"]
original = base["/etc/nginx/sites-enabled/biotact"]["text"].encode()
# Use the actual production renderer before adapting TLS/listeners for this fixture.
closed = gate.render(original).decode()


def fixture(text):
    import re

    text = text.replace("http://127.0.0.1:3001", "http://127.0.0.1:" + str(upstream.server_port))
    text = text.replace("    listen 443 ssl;", "    listen 127.0.0.1:" + str(port) + ";")
    text = re.sub(
        r"^    (?:ssl_certificate|ssl_certificate_key|ssl_dhparam|include /etc/letsencrypt/).*$",
        "",
        text,
        flags=re.M,
    )
    text = text[: text.index("\nserver {\n    if ($host")]
    text = text.replace("/var/log/nginx/biotact-referral-private.log", "/tmp/fixture-private.log")  # noqa: S108 - owned container
    return (
        "worker_processes 1; pid /tmp/tls-nginx.pid; error_log /tmp/tls-nginx.log notice; "
        "events { worker_connections 128; } http {\n" + text + "\n}\n"
    ).encode()


opened, gated = fixture(original.decode()), fixture(closed)
site = WORK / "nginx.conf"
site.write_bytes(opened)
mounts = ["-v", str(WORK) + ":/fixture:ro"]
for filename in ("security_headers.conf", "biotact_entry_security_headers.conf"):
    path = WORK / filename
    path.write_text(base["/etc/nginx/snippets/" + filename]["text"])
    mounts += ["-v", str(path) + ":/etc/nginx/snippets/" + filename + ":ro"]


def run(args):
    return subprocess.check_output(args, text=True, timeout=15).strip()


def request(host, method="POST", path="/api/v1/orders"):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    connection.request(
        method, path, body="{}" if method == "POST" else None, headers={"Host": host}
    )
    response = connection.getresponse()
    status, headers = response.status, dict(response.getheaders())
    response.read()
    connection.close()
    return status, headers


def lease():
    return {
        "observed_at": time.time(),
        "minimum_valid_until": time.time() + 300,
        "gate_effective": True,
        "renewal_healthy": True,
        "old_nginx_workers": [],
    }


reloads = []


def command(args):
    if args[:2] == ["/usr/bin/systemctl", "reload"]:
        reloads.append(time.time())
        return run(["podman", "kill", "--signal", "HUP", NAME])
    if args == ["/usr/sbin/nginx", "-t"]:
        return run(["podman", "exec", NAME, "nginx", "-t", "-c", "/fixture/nginx.conf"])
    target = urlsplit(args[-1])
    assert args[0] == "curl"
    assert target.hostname in {"app.biotact.uz", "admin.biotact.uz"}
    return str(request(target.hostname, path=target.path)[0])


created = False
buffered = None
future = None
pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
try:
    run(
        [
            "podman",
            "run",
            "--pull=never",
            "--name",
            NAME,
            "--network",
            "host",
            "--security-opt",
            "label=disable",
            "-d",
            *mounts,
            IMAGE,
            "nginx",
            "-c",
            "/fixture/nginx.conf",
            "-g",
            "daemon off;",
        ]
    )
    created = True
    version = subprocess.run(
        ["podman", "exec", NAME, "nginx", "-v"], capture_output=True, text=True, check=True
    )
    print("ACTUAL_NGINX_VERSION", version.stderr.strip(), flush=True)
    master = int(run(["podman", "inspect", "--format", "{{.State.Pid}}", NAME]))
    pidfile = WORK / "master.pid"
    pidfile.write_text(str(master))

    def children():
        return [
            close.process(int(p))
            for p in Path(f"/proc/{master}/task/{master}/children").read_text().split()
            if close.process(int(p)) is not None
        ]

    deadline = time.monotonic() + 5
    while not children():
        assert time.monotonic() < deadline
        time.sleep(0.02)
    first = children()[0]
    binding_dir = WORK / "binding"
    binding_dir.mkdir()
    effective = subprocess.run(
        ["podman", "exec", NAME, "nginx", "-T", "-c", "/fixture/nginx.conf"],
        capture_output=True,
        check=True,
    )
    binding = {
        "master": close.process(master),
        "old_workers": [first],
        "phase": "captured",
        "site_resolved": str(site.resolve()),
        "base_sha256": hashlib.sha256(opened).hexdigest(),
        "closed_sha256": hashlib.sha256(gated).hexdigest(),
        "prior_effective_sha256": hashlib.sha256(effective.stdout + effective.stderr).hexdigest(),
        "uid": site.stat().st_uid,
        "gid": site.stat().st_gid,
        "mode": 0o644,
    }
    (binding_dir / "binding.json").write_text(json.dumps(binding))
    (binding_dir / "lease.json").write_text(json.dumps(lease()))
    # Same counterexample as the Reviewer: A exits, master respawns B before close.
    run(["podman", "exec", NAME, "sh", "-c", "kill -KILL $(cat /proc/1/task/1/children)"])
    deadline = time.monotonic() + 5
    while not children() or first in children():
        assert time.monotonic() < deadline
        time.sleep(0.02)
    replacement = children()[0]
    buffered = socket.create_connection(("127.0.0.1", port), timeout=5)
    buffered.sendall(
        b"POST /api/v1/orders HTTP/1.1\r\nHost: app.biotact.uz\r\n"
        b"Content-Length: 2\r\nExpect: 100-continue\r\nX-TLS-Buffered: yes\r\nConnection: close\r\n\r\n"
    )
    assert b"100 Continue" in buffered.recv(4096)
    print("REPLACEMENT_WORKER_BUFFERED_POST_CONFIRMED", first, replacement, flush=True)
    actual_run = subprocess.run

    def config_run(args, **kwargs):
        if args == ["/usr/sbin/nginx", "-T"]:
            return effective
        return actual_run(args, **kwargs)

    with (
        patch.object(close, "SITE", site),
        patch.object(close, "PIDFILE", pidfile, create=True),
        patch.object(close, "render", return_value=gated),
        patch.object(close, "command", side_effect=command),
        patch.object(close, "lease", side_effect=lease),
        patch.object(close.subprocess, "run", side_effect=config_run),
        patch.object(drain, "stop_old_writer") as stop,
    ):
        close.close(binding_dir)
        deadline = time.monotonic() + 5
        while request("app.biotact.uz")[0] != 423:
            assert time.monotonic() < deadline
            time.sleep(0.02)
        future = pool.submit(close.wait_closed, binding_dir)
        deadline = time.monotonic() + 7
        while not future.done():
            saved = json.loads((binding_dir / "binding.json").read_text())
            if saved["phase"] == "fenced":
                break
            assert time.monotonic() < deadline, "generation fence did not complete"
            time.sleep(0.02)
        if future.done():
            accepted = future.result()
            assert accepted["old_nginx_workers"] == []
            assert close.process(replacement["pid"]) == replacement
            print("INCORRECT_PREFLIGHT_ACCEPTED_WITH_BUFFERED_REPLACEMENT", accepted, flush=True)
        assert not future.done(), "barrier passed while unobserved old-config worker/body survived"
        saved = json.loads((binding_dir / "binding.json").read_text())
        assert replacement in saved["barrier_workers"], saved
        assert close.process(replacement["pid"]) == replacement
        try:
            close.preflight(binding_dir)
        except ValueError as exc:
            detail = str(exc)
        else:
            raise AssertionError("preflight accepted buffered old-config request")
        assert "old Nginx workers" in detail, detail
        assert not any(row[3] == "yes" for row in requests)
        print("WAIT_AND_PREFLIGHT_REFUSED_BUFFERED_OLD_CONFIG_WORKER", flush=True)
        # The admitted old request must end before the barrier. Record actual writer IO.
        before_body = len(requests)
        buffered.sendall(b"{}")
        response = b""
        while chunk := buffered.recv(4096):
            response += chunk
        buffered.close()
        buffered = None
        assert b"200 OK" in response, response
        assert len(requests) == before_body + 1
        assert sum(row[3] == "yes" for row in requests) == 1
        result = future.result(timeout=8)
        assert result["old_nginx_workers"] == []
        assert close.process(replacement["pid"]) is None
        stop.assert_not_called()
        print("BUFFERED_REQUEST_ENDED_BEFORE_IDENTITY_BARRIER_PASSED", len(reloads), flush=True)
        # Changed master is a refusal even after otherwise successful drain.
        pidfile.write_text(str(master + 999999))
        try:
            close.preflight(binding_dir)
        except ValueError as exc:
            detail = str(exc)
        else:
            raise AssertionError("changed master accepted")
        assert "master changed" in detail
        pidfile.write_text(str(master))
        for host in ("app.biotact.uz", "admin.biotact.uz"):
            for path in (
                "/api/v1/orders",
                "/api/v1/orders/",
                "/api/v1/%6frders",
                "/api//v1/orders",
            ):
                before = len(requests)
                status, headers = request(host, path=path)
                assert status == 423
                assert len(requests) == before
                assert headers["X-Frame-Options"] == "SAMEORIGIN"
                assert headers["X-Content-Type-Options"] == "nosniff"
                assert "max-age=31536000" in headers["Strict-Transport-Security"]
                assert headers["Referrer-Policy"] == (
                    "no-referrer" if host.startswith("app.") else "strict-origin-when-cross-origin"
                )
            for method, path in [
                ("GET", "/api/v1/orders"),
                ("POST", "/api/v1/orders/quote"),
                ("POST", "/api/v1/payments/create"),
                ("POST", "/api/v1/payments/callback"),
                ("POST", "/api/v1/payments/webhook"),
                ("POST", "/api/v1/internal/employee-referrals/snapshots"),
            ]:
                assert request(host, method, path)[0] == 200
        assert site.read_bytes() == gated
        print(
            "BOTH_HOSTS_423_HEADERS_PRESERVED_ROUTES_AND_CHANGED_MASTER_REFUSAL_PASSED", flush=True
        )
finally:
    if buffered is not None:
        buffered.close()
    if future is not None:
        with contextlib.suppress(ValueError, TimeoutError):
            future.result(timeout=8)
    pool.shutdown(wait=True)
    if created:
        subprocess.run(
            ["podman", "stop", "--time", "2", NAME], capture_output=True, timeout=10, check=False
        )
    upstream.shutdown()
    upstream.server_close()
    server_thread.join(timeout=5)
