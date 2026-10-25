"""Exercise the actual remote deploy body with local CLI/service substitutes."""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "ops/shop-release-assets.py"
BASE = "1" * 40
TARGET = "2" * 40
OWNERS = ("packages/frontend", "admin-panel-v2")
BOOTSTRAP_BYTES = b"synthetic pip wheel: never executed or installed\n"

STUB = r"""
import json, os, pathlib, shutil, signal, sys
name=pathlib.Path(sys.argv[0]).name; a=sys.argv[1:]
if name=='python' and a[:2]!=['-m','pip']:
 if a and a[0]=='ops/employee-referral-runtime.py':sys.stdin.read()
 sys.exit(0)
r=pathlib.Path(os.environ['FIXTURE']); p=r/'state.json'; s=json.loads(p.read_text()); cwd=pathlib.Path.cwd()
failure=os.environ.get('FAILURE',''); code=0; output=''
s['events'].append([name,*a]); base='1'*40; target='2'*40
if name=='git':
 if a[:2]==['rev-parse','HEAD']:output=s['commit']
 elif a[0]=='show':output=pathlib.Path(os.environ['ASSET_HELPER_SOURCE']).read_text()
 elif a[0]=='reset':s['commit']=base if a[-1]==base else target
 elif a[0]=='fetch' and failure=='fetch':code=1
elif name=='npm':
 owner='frontend' if cwd.name=='frontend' else 'admin'
 if a[0]=='ci' and failure==owner+'-install':code=1
 elif a[:2]==['run','build']:
  dist=cwd/'dist'; shutil.rmtree(dist); dist.mkdir()
  (dist/'index.html').write_text('candidate'); (dist/'candidate.js').write_text('candidate')
  if failure==owner+'-build':code=1
  if failure=='signal' and owner=='admin':os.kill(os.getppid(),signal.SIGTERM)
elif name=='pip':
 owner='api' if cwd.name=='api' else 'bot'
 if failure==owner+'-install' or (owner=='api' and failure in ['refused','tampered']):
  if failure=='tampered':
   archived=max((r/'.venv/shop-release-assets').glob('snapshot-*/assets/packages/frontend/index.html'),key=lambda p:p.stat().st_mtime_ns)
   archived.write_text('corrupt')
  code=1
elif name=='python' and a[:2]==['-m','pip']:
 if failure=='bootstrap-install':code=1
elif name=='alembic' and failure=='migration':code=1
elif name=='curl':
 if '--output' in a:
  if failure=='bootstrap-download':code=1
  elif failure!='bootstrap-missing':
   artifact=pathlib.Path(a[a.index('--output')+1])
   artifact.write_bytes(pathlib.Path(os.environ['FIXTURE_PIP_WHEEL']).read_bytes())
   if failure=='bootstrap-corrupt':artifact.write_bytes(b'corrupt')
 elif failure=='health' and s['commit']==target:code=22
elif name=='sudo':
 command=a[a.index('--')+2:]; operation=command[0]
 if operation=='begin':s['guard']=True
 elif operation=='compatible' and failure=='refused':code=1
 elif operation=='finish':
  if failure=='finish' and s['commit']==target:code=1
  else:s['guard']=False
elif name=='pm2':
 operation=a[0]; service=a[1] if len(a)>1 else ''
 if operation=='jlist':output='[]'
 elif operation=='pid':output='123' if s['services']['worker']=='online' else '0'
 elif operation=='stop':s['services']['worker' if service=='employee-referral-worker' else service]='stopped'
 elif operation=='startOrRestart':s['services']['worker']='online'
 elif operation=='restart':
  if service=='api':
   if s['commit']==base:
    for owner in ['packages/frontend','admin-panel-v2']:
     dist=r/owner/'dist'
     if not (dist/'index.html').exists() or (dist/'index.html').read_text()!='baseline' or (dist/'candidate.js').exists():
      s['incoherent'].append(owner)
   if failure=='start' and s['commit']==target:code=1
  if code==0:s['services'][service]='online'
p.write_text(json.dumps(s)); print(output,end='\n' if output else ''); sys.exit(code)
"""


class DeploymentTests(unittest.TestCase):
    def setUp(self) -> None:
        work = ROOT / ".venv/s2-work/f40-tests"
        work.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=work)
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.bin = self.project / "bin"
        self.bin.mkdir()
        stub = "#!" + sys.executable + "\n" + STUB
        for command in ("git", "npm", "pip", "python", "alembic", "curl", "sudo", "pm2", "sleep"):
            path = self.bin / command
            path.write_text(stub)
            path.chmod(0o755)
        for owner in OWNERS:
            dist = self.project / owner / "dist"
            dist.mkdir(parents=True)
            (dist / "index.html").write_text("baseline")
            (dist / "old.js").write_text("baseline")
            (dist / "old.js").chmod(0o640)
        for owner in ("packages/api", "packages/bot"):
            venv = self.project / owner / "venv/bin"
            venv.mkdir(parents=True)
            (venv / "activate").write_text("deactivate() { :; }\n")
            (venv / "python").write_text(stub)
            (venv / "python").chmod(0o755)
        ops = self.project / "ops"
        ops.mkdir()
        release = (ROOT / "ops/employee-referral-release.sh").read_text()
        (ops / "employee-referral-release.sh").write_text(
            release.replace("/var/www/biotact", str(self.project))
        )
        guard = self.project / "guard"
        guard.touch(mode=0o755)
        self.state = self.project / "state.json"
        self.state.write_text(
            json.dumps(
                {
                    "commit": BASE,
                    "guard": False,
                    "events": [],
                    "incoherent": [],
                    "services": {"api": "online", "bot": "online", "worker": "online"},
                }
            )
        )
        self.wheel = self.project / "synthetic-pip.whl"
        self.wheel.write_bytes(BOOTSTRAP_BYTES)
        self.env = {
            **os.environ,
            "FIXTURE": str(self.project),
            "FIXTURE_PIP_WHEEL": str(self.wheel),
            "ASSET_HELPER_SOURCE": str(HELPER),
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def deploy(self, failure: str = "") -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
        workflow = Path(
            os.environ.get("SHOP_DEPLOY_WORKFLOW", ROOT / ".github/workflows/deploy.yml")
        )
        # Execute the actual heredoc, changing only fixture paths and the event value.
        text = workflow.read_text().split("<< 'SCRIPT'\n", 1)[1].split("\n          SCRIPT", 1)[0]
        text = "\n".join(line[10:] for line in text.splitlines())
        text = text.replace("/var/www/biotact", str(self.project))
        text = text.replace(
            "/usr/local/sbin/biotact-referral-maintenance", str(self.project / "guard")
        )
        text = text.replace("${{ github.event.workflow_run.head_sha }}", TARGET)
        # Substitute only the expected digest of deterministic external fixture bytes.
        digests = re.findall(r"printf '%s  %s\\n' ([0-9a-f]{64})", text)
        assert len(digests) == 2
        assert len(set(digests)) == 1
        text = text.replace(digests[0], hashlib.sha256(BOOTSTRAP_BYTES).hexdigest())
        # The source is the trusted checked-in workflow; all affected CLIs use local substitutes.
        result = subprocess.run(  # noqa: S603
            ["/bin/bash", "-c", text],
            check=False,
            cwd=self.project,
            env={**self.env, "FAILURE": failure},
            capture_output=True,
            text=True,
            timeout=20,
        )
        state = json.loads(self.state.read_text())
        return result, state

    def helper(self, action: str, *args: str) -> subprocess.CompletedProcess[str]:
        # Execute only the checked-in helper with explicit synthetic fixture arguments.
        return subprocess.run(  # noqa: S603
            [
                sys.executable,
                "-B",
                str(HELPER),
                action,
                "--project",
                str(self.project),
                *args,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def assert_restored(self, state: dict[str, Any]) -> None:
        assert state["commit"] == BASE
        assert not state["guard"]
        assert state["services"]["api"] == "online"
        assert state["services"]["worker"] == "online"
        assert state["incoherent"] == [], "old source was resumed with candidate/partial assets"
        for owner in OWNERS:
            dist = self.project / owner / "dist"
            assert (dist / "index.html").read_text() == "baseline"
            assert sorted(p.name for p in dist.iterdir()) == ["index.html", "old.js"]
            assert (dist / "old.js").stat().st_mode & 0o777 == 0o640

    def test_restore_precedes_old_api_resume(self) -> None:
        result, state = self.deploy("api-install")
        assert result.returncode != 0
        self.assert_restored(state)

    def test_success_retains_source_bound_verified_artifacts(self) -> None:
        result, state = self.deploy()
        assert result.returncode == 0, result.stderr
        assert state["commit"] == TARGET
        downloads = [e for e in state["events"] if e[0] == "curl" and "--output" in e]
        installs = [e for e in state["events"] if e[:3] == ["python", "-m", "pip"]]
        assert len(downloads) == len(installs) == 2
        assert not state["guard"]
        snapshots = list(
            (self.project / ".venv/shop-release-assets").glob("snapshot-*/manifest.json")
        )
        assert {json.loads(p.read_text())["commit"] for p in snapshots} == {BASE, TARGET}
        assert all(json.loads(p.read_text()).get("successful_at") for p in snapshots)

    def test_failures_restore_both_artifacts_and_keep_failure_visible(self) -> None:
        for failure in (
            "bootstrap-download",
            "bootstrap-missing",
            "bootstrap-corrupt",
            "bootstrap-install",
            "frontend-install",
            "frontend-build",
            "admin-install",
            "admin-build",
            "migration",
            "bot-install",
            "start",
            "health",
            "finish",
            "signal",
        ):
            with self.subTest(failure=failure):
                result, state = self.deploy(failure)
                assert result.returncode != 0, result.stdout
                self.assert_restored(state)
                if failure.startswith("bootstrap-"):
                    assert not any(e[0] == "alembic" for e in state["events"])
                    assert "Deploy complete" not in result.stdout
                    if failure != "bootstrap-install":
                        assert not any(e[:3] == ["python", "-m", "pip"] for e in state["events"])
                self.state.write_text(
                    json.dumps(
                        {
                            "commit": BASE,
                            "guard": False,
                            "events": [],
                            "incoherent": [],
                            "services": {
                                "api": "online",
                                "bot": "online",
                                "worker": "online",
                            },
                        }
                    )
                )
                for owner in OWNERS:
                    dist = self.project / owner / "dist"
                    for path in dist.iterdir():
                        path.unlink()
                    (dist / "index.html").write_text("baseline")
                    (dist / "old.js").write_text("baseline")
                    (dist / "old.js").chmod(0o640)

    def test_refused_compatibility_or_corrupt_snapshot_never_resumes_api(self) -> None:
        for failure in ("refused", "tampered"):
            with self.subTest(failure=failure):
                result, state = self.deploy(failure)
                assert result.returncode != 0
                assert state["commit"] == TARGET
                assert state["guard"]
                assert state["services"]["api"] == "stopped"
                assert state["services"]["worker"] == "stopped"
                assert state["incoherent"] == []
                self.state.write_text(
                    json.dumps(
                        {
                            "commit": BASE,
                            "guard": False,
                            "events": [],
                            "incoherent": [],
                            "services": {
                                "api": "online",
                                "bot": "online",
                                "worker": "online",
                            },
                        }
                    )
                )
                for owner in OWNERS:
                    dist = self.project / owner / "dist"
                    for path in dist.iterdir():
                        path.unlink()
                    (dist / "index.html").write_text("baseline")
                    (dist / "old.js").write_text("baseline")
                    (dist / "old.js").chmod(0o640)

    def test_missing_baseline_or_fetch_failure_does_not_stop_services(self) -> None:
        result, state = self.deploy("fetch")
        assert result.returncode != 0
        assert not any(e[:2] == ["pm2", "stop"] for e in state["events"])
        (self.project / "admin-panel-v2/dist/index.html").unlink()
        result, state = self.deploy()
        assert result.returncode != 0
        assert not any(e[:2] == ["pm2", "stop"] for e in state["events"])
        assert state["services"]["api"] == "online"

    def test_three_successful_versions_keep_unaccepted_and_unresolved(self) -> None:
        unknown = self.project / ".venv/shop-release-assets/snapshot-foreign"
        unknown.mkdir(parents=True)
        (unknown / "manifest.json").write_text('{"kind":"foreign"}')
        pending = self.helper("capture", "--commit", "a" * 40)
        assert pending.returncode == 0, pending.stderr
        pending_path = Path(pending.stdout.strip())
        unresolved = self.helper("capture", "--commit", "b" * 40)
        unresolved_path = Path(unresolved.stdout.strip())
        assert self.helper("success", "--snapshot", str(unresolved_path)).returncode == 0
        assert self.helper("restore", "--snapshot", str(unresolved_path)).returncode == 0
        for digit in "34567":
            captured = self.helper("capture", "--commit", digit * 40)
            assert captured.returncode == 0, captured.stderr
            success = self.helper("success", "--snapshot", captured.stdout.strip(), "--prune")
            assert success.returncode == 0, success.stderr
        manifests = [
            json.loads(p.read_text())
            for p in (self.project / ".venv/shop-release-assets").glob("snapshot-*/manifest.json")
        ]
        ordinary = [
            m["commit"] for m in manifests if m.get("successful_at") and not m.get("recovery_open")
        ]
        assert unknown.exists()
        assert set(ordinary) == {d * 40 for d in "567"}
        assert pending_path.exists()
        assert unresolved_path.exists()

    def test_symlink_assets_are_refused_without_reading_their_target(self) -> None:
        outside = self.project / "private"
        outside.write_text("must not be captured")
        (self.project / "packages/frontend/dist/secret-link").symlink_to(outside)
        result = self.helper("capture", "--commit", BASE)
        assert result.returncode != 0
        assert outside.read_text() == "must not be captured"


if __name__ == "__main__":
    unittest.main()
