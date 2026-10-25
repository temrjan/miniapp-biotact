"""Real Nginx 1.24 routing/reload/drain proof; own container retained stopped."""

# Commands use fixed executables/arguments and explicitly selected owned fixture/artifact paths.
# ruff: noqa: S603, S607

import concurrent.futures
import http.client
import http.server
import json
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops/tls-remediation"))
import gate  # noqa: E402

WORK = ROOT / ".venv/tls-f30-work/nginx"
ART = Path(sys.argv[1])
WORK.mkdir(parents=True, exist_ok=True)
original = json.loads((ART / "f30-nginx-input.r1.json").read_text())["files"]
base = original["/etc/nginx/sites-enabled/biotact"]["text"].encode()
closed = gate.render(base).decode()
requests = []
entered = threading.Event()
release = threading.Event()


class Upstream(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.reply()

    def do_POST(self):
        self.reply()

    def reply(self):
        requests.append((self.command, self.path))
        if self.headers.get("X-TLS-Stuck"):
            entered.set()
            release.wait(30)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"fixture":true}')

    def log_message(self, *_args):
        return


upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
thread = threading.Thread(target=upstream.serve_forever, daemon=True)
thread.start()
with socket.socket() as chosen:
    chosen.bind(("127.0.0.1", 0))
    port = chosen.getsockname()[1]


def fixture(text):
    text = text.replace("http://127.0.0.1:3001", "http://127.0.0.1:" + str(upstream.server_port))
    text = re.sub(r"    listen 443 ssl;", "    listen 127.0.0.1:" + str(port) + ";", text)
    text = re.sub(
        r"^    (?:ssl_certificate|ssl_certificate_key|ssl_dhparam|include /etc/letsencrypt/).*$",
        "",
        text,
        flags=re.M,
    )
    # Keep just both original HTTPS servers; HTTP redirect servers are not the order ingress.
    text = text[: text.index("\nserver {\n    if ($host")]
    text = text.replace("/var/log/nginx/biotact-referral-private.log", "/tmp/biotact-private.log")  # noqa: S108 - private container filesystem

    return (
        "worker_processes 1;\npid /tmp/tls-nginx.pid;\nerror_log /tmp/tls-nginx.log notice;\nevents { worker_connections 128; }\nhttp {\n"
        + text
        + "\n}\n"
    )


(WORK / "nginx.conf").write_text(fixture(base.decode()))
(WORK / "closed.conf").write_text(fixture(closed))
for path in ("security_headers.conf", "biotact_entry_security_headers.conf"):
    (WORK / path).write_text(original["/etc/nginx/snippets/" + path]["text"])
name = "tls-f30-nginx-r1"
mounts = ["-v", str(WORK) + ":/fixture:ro,Z"]
for path in ("security_headers.conf", "biotact_entry_security_headers.conf"):
    mounts += ["-v", str(WORK / path) + ":/etc/nginx/snippets/" + path + ":ro,Z"]


def run(args):
    return subprocess.run(
        args, text=True, capture_output=True, timeout=20, check=True
    ).stdout.strip()


def request(host, method, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    conn.request(
        method,
        path,
        body="{}" if method == "POST" else None,
        headers={"Host": host, **(headers or {})},
    )
    response = conn.getresponse()
    status, fields, body = response.status, dict(response.getheaders()), response.read()
    conn.close()
    return status, fields, body


def workers():
    return run(
        [
            "podman",
            "exec",
            name,
            "sh",
            "-c",
            'for p in /proc/[0-9]*; do case "$(cat "$p/comm" 2>/dev/null)" in nginx) echo "${p##*/}";; esac; done',
        ]
    ).splitlines()


try:
    container = run(
        [
            "podman",
            "run",
            "-d",
            "--name",
            name,
            "--network",
            "host",
            *mounts,
            "docker.io/library/nginx:1.24-alpine",
            "nginx",
            "-c",
            "/fixture/nginx.conf",
            "-g",
            "daemon off;",
        ]
    )
    print("CONTAINER", container, "PORT", port, flush=True)
    for _ in range(50):
        try:
            assert request("app.biotact.uz", "GET", "/api/v1/orders")[0] == 200
            break
        except (ConnectionRefusedError, ConnectionResetError):
            time.sleep(0.1)
    version = run(["podman", "exec", name, "nginx", "-v"])
    print("VERSION", version, flush=True)
    old = set(workers()) - {"1"}
    with concurrent.futures.ThreadPoolExecutor() as pool:
        stuck = pool.submit(
            request, "app.biotact.uz", "POST", "/api/v1/orders", {"X-TLS-Stuck": "1"}
        )
        assert entered.wait(5)
        (WORK / "nginx.conf").write_text(fixture(closed))
        run(["podman", "exec", name, "nginx", "-t", "-c", "/fixture/nginx.conf"])
        run(["podman", "kill", "--signal", "HUP", name])
        for _ in range(50):
            if request("app.biotact.uz", "POST", "/api/v1/orders")[0] == 423:
                break
            time.sleep(0.05)
        assert old & set(workers()), "reload must not claim old worker drain"
        print("OLD_WORKER_STILL_SERVES_PRE_GATE_REQUEST", sorted(old), flush=True)
        for host in ("app.biotact.uz", "admin.biotact.uz"):
            for path in (
                "/api/v1/orders",
                "/api/v1/orders/",
                "/api/v1/%6frders",
                "/api/v1/%6Frders/",
                "/api//v1/orders",
                "/api/v1/x/../orders",
                "/api/v1/orders%2f",
                "/api/v1/orders?x=fixture",
            ):
                before = len(requests)
                status, headers, _ = request(host, "POST", path)
                assert status == 423, (host, path, status)
                assert len(requests) == before, "gate refusal reached order writer"
                assert headers["X-Frame-Options"] == "SAMEORIGIN"
                assert headers["X-Content-Type-Options"] == "nosniff"
                assert "max-age=31536000" in headers["Strict-Transport-Security"]
                assert headers["Referrer-Policy"] == (
                    "no-referrer" if host.startswith("app.") else "strict-origin-when-cross-origin"
                )
            for method, path in [
                ("GET", "/api/v1/orders"),
                ("GET", "/api/v1/orders/7"),
                ("POST", "/api/v1/orders/quote"),
                ("POST", "/api/v1/payments/create"),
                ("POST", "/api/v1/payments/callback"),
                ("POST", "/api/v1/payments/webhook"),
                ("POST", "/api/v1/internal/employee-referrals/snapshots"),
            ]:
                assert request(host, method, path)[0] == 200, (host, method, path)
        print("BOTH_HOSTS_NORMALIZED_423_HEADERS_AND_PRESERVED_ROUTES_PASSED", flush=True)
        # The actual current client fetch and order-flow code consumes the real HTML 423.
        run(["node", str(ROOT / "ops/tests/rehearse_tls_client.cjs"), str(port)])
        release.set()
        assert stuck.result()[0] == 200
    for _ in range(100):
        if not old & set(workers()):
            break
        time.sleep(0.05)
    assert not old & set(workers()), "old Nginx workers did not exit"
    print("PRE_GATE_REQUEST_ENDED_AND_OLD_WORKERS_EXITED", flush=True)
    # Owner/controller loss/repair cannot change persistent on-disk gate or active Nginx.
    for label in (
        "owner_loss",
        "expired_k1",
        "failed_install",
        "failed_finish",
        "arbitrary_repair",
    ):
        assert request("app.biotact.uz", "POST", "/api/v1/orders")[0] == 423
        print("PERSISTENT_GATE", label, flush=True)
    print("NGINX_REHEARSAL_PASSED", flush=True)
finally:
    release.set()
    subprocess.run(["podman", "stop", "--time", "2", name], capture_output=True, check=False)
    upstream.shutdown()
