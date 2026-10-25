"""Adversarial gate predicates and package-delta/installer refusal contracts."""

# unittest keeps this operator fixture independent of an application environment.
# ruff: noqa: PT027

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HELPERS = Path(__file__).resolve().parents[1] / "tls-remediation"
sys.path.insert(0, str(HELPERS))
import close  # noqa: E402
import drain  # noqa: E402
import gate  # noqa: E402
import observe  # noqa: E402
import reopen  # noqa: E402

import packages  # noqa: E402


def observed(now=1000):
    row = {
        "revision": 1,
        "content_sha256": "a" * 64,
        "participants": 2,
        "issued_at": 990,
        "valid_until": 1290,
        "blocked": False,
        "ack_matches": True,
        "receipt_matches": True,
    }
    consumers = {
        role: {"pid": i + 1, "start_ticks": 10, "venv": "/fixture/venv", "healthy": True}
        for i, role in enumerate(
            ("shop-api", "shop-worker", "shop-bot", "team-api", "team-worker", "team-bot")
        )
    }
    binding = {
        "consumers": consumers,
        "releases": {"shop": "base", "team": "release"},
        "controller": None,
        "timers": {"timers": True},
        "registrations": {"shop": "registration", "portal": "registration"},
        "configuration": {"config": [1, 2, 3]},
    }
    return {
        "readiness": {
            "started_at": now - 8,
            "completed_at": now,
            "boundary": 980,
            "before": copy.deepcopy(binding),
            "after": copy.deepcopy(binding),
        },
        "observed_at": now,
        "gate_effective": True,
        "finished": ["shop", "portal"],
        "controller_absent": True,
        "release_hold": True,
        "trust_passed": True,
        "health": {"receivers": True, "timers": True},
        "consumers": consumers,
        "releases": {"shop": "base", "team": "release"},
        "renewal_healthy": True,
        "source": {"7": copy.deepcopy(row)},
        "source_after": {"7": copy.deepcopy(row)},
        "replica": {"7": copy.deepcopy(row)},
    }


class GateContract(unittest.TestCase):
    def setUp(self):
        self.current = observed()
        self.previous = observed(970)
        self.previous["source"]["7"]["issued_at"] = 960

    def test_post_resume_renewal_and_complete_replica_open(self):
        gate.reopening(self.previous, self.current, 980, 1000)

    def test_owner_loss_failed_finish_expiry_health_and_partial_replica_refuse(self):
        mutations = [
            lambda d: d.update(controller_absent=False),
            lambda d: d.update(finished=["shop"]),
            lambda d: d.update(renewal_healthy=False),
            lambda d: d.update(release_hold=False),
            lambda d: d.update(trust_passed=False),
            lambda d: d.update(observed_at=900),
            lambda d: d["replica"].clear(),
            lambda d: d["source_after"].clear(),
            lambda d: d["consumers"].pop("team-bot"),
            lambda d: d["health"].update(timers=False),
            lambda d: d["source"]["7"].update(issued_at=970),
            lambda d: d["source"]["7"].update(valid_until=999),
            lambda d: d["source"]["7"].update(ack_matches=False),
            lambda d: d["replica"]["7"].update(receipt_matches=False),
            lambda d: d["replica"]["7"].update(content_sha256="b" * 64),
            lambda d: d["consumers"]["shop-api"].update(start_ticks=11),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                candidate = copy.deepcopy(self.current)
                mutate(candidate)
                with self.assertRaises(ValueError):
                    gate.reopening(self.previous, candidate, 980, 1000)

    def test_two_stage_freshness_and_continuity_refusals(self):
        mutations = [
            lambda d: d["readiness"].update(completed_at=994),
            lambda d: d["readiness"].update(started_at=970),
            lambda d: d["readiness"].update(boundary=979),
            lambda d: d.update(observed_at=1001),
            lambda d: d["readiness"]["after"]["consumers"]["team-bot"].update(pid=99),
            lambda d: d["readiness"]["after"]["consumers"]["team-bot"].update(start_ticks=99),
            lambda d: d["readiness"]["after"]["consumers"]["team-bot"].update(venv="other"),
            lambda d: d["readiness"]["after"]["registrations"].update(shop="other"),
            lambda d: d["readiness"]["after"]["configuration"].update(config=[4]),
            lambda d: d["readiness"]["after"]["timers"].update(timers=False),
            lambda d: d["readiness"]["after"].update(controller="operation"),
            lambda d: d["readiness"]["after"]["releases"].update(shop="other"),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                candidate = copy.deepcopy(self.current)
                mutate(candidate)
                with self.assertRaises(ValueError):
                    gate.reopening(self.previous, candidate, 980, 1000)
        # Updating only the critical timestamp cannot renew expired health evidence.
        self.current["observed_at"] = 1006
        with self.assertRaisesRegex(ValueError, "readiness stale"):
            gate.reopening(self.previous, self.current, 980, 1006)
        with self.assertRaises(ValueError):
            gate.reopening(self.current, self.current, 980, 1006)

    def test_real_snapshot_path_accepts_slow_health_but_refuses_continuity_break(self):
        binding = self.current["readiness"]["before"]
        receipt = {k: self.current[k] for k in ("finished", "release_hold", "trust_passed")}
        clock = [992.0]

        def slow_health(boundary):
            assert boundary == 980
            clock[0] += 8
            return {"receivers": True}

        def run_snapshot(after):
            clock[0] = 992.0
            with (
                patch.object(observe, "continuity", side_effect=[binding, after]),
                patch.object(observe, "readiness", side_effect=slow_health),
                patch.object(observe.time, "time", side_effect=lambda: clock[0]),
                patch.object(observe, "command", side_effect=["423", "423", "base"]),
                patch.object(
                    observe,
                    "query",
                    side_effect=[self.current[k] for k in ("source", "replica", "source_after")],
                ),
                patch.object(observe, "TEAM", Path("/fixture/team")),
                patch.object(observe, "STATE", Path("/fixture/absent-state")),
            ):
                return observe.snapshot(receipt, 980)

        binding["releases"]["team"] = "/fixture/team"
        self.previous["releases"]["team"] = "/fixture/team"
        current = run_snapshot(copy.deepcopy(binding))
        gate.reopening(self.previous, current, 980, clock[0])
        assert current["readiness"]["completed_at"] - current["readiness"]["started_at"] == 8
        changed = copy.deepcopy(binding)
        changed["consumers"]["team-bot"]["pid"] += 1
        with self.assertRaisesRegex(ValueError, "changed during readiness"):
            run_snapshot(changed)

    def test_previous_snapshot_then_22_second_renewal_wait_then_real_reopen_check(self):
        binding = copy.deepcopy(self.current["readiness"]["before"])
        binding["releases"]["team"] = "/fixture/team"
        clock = [962.0]
        receipt = {k: self.current[k] for k in ("finished", "release_hold", "trust_passed")}

        def health(boundary):
            assert boundary == 950
            clock[0] += 8
            return {"receivers": True}

        def query(database, sql):
            assert database in ("biotact_db", "biotact_team_db")
            assert sql in (observe.SOURCE_SQL, observe.REPLICA_SQL)
            row = copy.deepcopy(self.current["source"])
            row["7"]["issued_at"] = 960 if clock[0] < 990 else 990
            return row

        with (
            patch.object(observe, "continuity", return_value=binding),
            patch.object(observe, "readiness", side_effect=health),
            patch.object(observe.time, "time", side_effect=lambda: clock[0]),
            patch.object(observe, "command", side_effect=["423", "423", "base"] * 2),
            patch.object(observe, "query", side_effect=query),
            patch.object(observe, "TEAM", Path("/fixture/team")),
            patch.object(observe, "STATE", Path("/fixture/absent-state")),
        ):
            previous = observe.snapshot(receipt, 950)
            clock[0] += 22
            current = observe.snapshot(receipt, 950)
        assert clock[0] - previous["observed_at"] == 30
        gate.reopening(previous, current, 950, clock[0])
        with self.assertRaisesRegex(ValueError, "stale"):
            gate.reopening(previous, current, 950, clock[0] + 6)

    def test_readiness_rejects_each_false_portal_flag(self):
        portal = dict.fromkeys(("enabled", "configuration_valid", "ready", "fresh"), True)
        for field in portal:
            with (
                self.subTest(field=field),
                patch.object(
                    observe,
                    "command",
                    side_effect=["", "", json.dumps(dict(portal, **{field: False}))],
                ),
                self.assertRaisesRegex(ValueError, "readiness unavailable"),
            ):
                observe.readiness(980)

    def test_failed_mandatory_readiness_never_samples_or_opens(self):
        with (
            patch.object(observe, "continuity", return_value={}),
            patch.object(observe, "readiness", side_effect=ValueError("health failed")),
            patch.object(observe, "query") as query,
            self.assertRaisesRegex(ValueError, "health failed"),
        ):
            observe.snapshot({}, 980)
        query.assert_not_called()

    def test_stuck_writer_sessions_and_open_nginx_block_global_pause(self):
        pre = {
            "gate_effective": True,
            "renewal_healthy": True,
            "old_nginx_workers": [],
            "observed_at": 1000,
            "minimum_valid_until": 1020,
        }
        gate.pre_pause(pre, 1000, 10)
        for field, value in [
            ("old_nginx_workers", [11]),
            ("renewal_healthy", False),
            ("minimum_valid_until", 1005),
            ("observed_at", 900),
        ]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                gate.pre_pause(dict(pre, **{field: value}), 1000, 10)
        end = {
            "gate_effective": True,
            "old_writer_alive": False,
            "old_writer_db_sessions": 0,
            "old_writer_db_transactions": 0,
            "pre_pause_deadline": 1020,
        }
        gate.drained(end, 1001)
        for field, value in [
            ("old_writer_alive", True),
            ("old_writer_db_sessions", 1),
            ("old_writer_db_transactions", 1),
            ("pre_pause_deadline", 999),
        ]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                gate.drained(dict(end, **{field: value}), 1001)

    def test_unknown_ingress_refused(self):
        with self.assertRaises(ValueError):
            gate.render(b"server { listen 8080; }")


class AtomicReopenContract(unittest.TestCase):
    def test_expired_final_clock_and_failed_reload_preserve_gate(self):
        work = HELPERS.parents[1] / ".git/tls-freshness-correction-r1/unit"
        work.mkdir(parents=True, exist_ok=True)
        for failure in ("final-clock", "reload"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(dir=work) as owned:
                directory = Path(owned)
                site = directory / "site"
                original, closed = b"original", b"closed"
                site.write_bytes(closed)
                (directory / "original.conf").write_bytes(original)
                (directory / "closed.conf").write_bytes(closed)
                stat = site.stat()
                (directory / "binding.json").write_text(
                    json.dumps(
                        {
                            "site_resolved": str(site),
                            "closed_sha256": hashlib.sha256(closed).hexdigest(),
                            "mode": stat.st_mode & 511,
                            "uid": stat.st_uid,
                            "gid": stat.st_gid,
                        }
                    )
                )
                previous, current = observed(970), observed()
                previous["source"]["7"]["issued_at"] = 960
                previous_file = directory / "previous.json"
                previous_file.write_text(json.dumps(previous))
                receipt = directory / "receipt.json"
                receipt.write_text("{}")
                commands = ["", ValueError("reload failed"), "", ""]
                with (
                    patch.object(reopen, "SITE", site),
                    patch.object(reopen, "BASE_SHA", hashlib.sha256(original).hexdigest()),
                    patch.object(reopen, "snapshot", return_value=current),
                    patch.object(
                        reopen.time,
                        "time",
                        side_effect=[1000, 1006] if failure == "final-clock" else [1000, 1000],
                    ),
                    patch.object(reopen, "command", side_effect=commands) as command,
                    patch.object(
                        sys,
                        "argv",
                        ["reopen", str(directory), str(receipt), str(previous_file), "980"],
                    ),
                    self.assertRaisesRegex(ValueError, "stale|reload failed"),
                ):
                    reopen.main()
                assert site.read_bytes() == closed
                assert command.call_count == (0 if failure == "final-clock" else 4)


class ProtectedWaitContract(unittest.TestCase):
    def test_unaccounted_shutting_down_worker_and_incomplete_fence_refuse(self):
        binding = {"barrier_workers": []}
        with patch.object(
            close, "workers", return_value=[{"pid": 9, "ticks": "10", "shutting_down": True}]
        ):
            assert close.remaining_workers(binding) == [{"pid": 9, "ticks": "10"}]
        work = HELPERS.parents[1] / ".venv/tls-f30-correction-r2/unit"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as owned:
            directory = Path(owned)
            (directory / "binding.json").write_text(json.dumps({"phase": "census"}))
            with self.assertRaisesRegex(ValueError, "fence incomplete"):
                close.preflight(directory)
            pidfile = directory / "nginx.pid"
            Path(str(pidfile) + ".oldbin").touch()
            with (
                patch.object(close, "PIDFILE", pidfile),
                self.assertRaisesRegex(ValueError, "binary upgrade"),
            ):
                close.workers({"master": {"pid": 7, "ticks": "8"}})

    def test_fence_reload_failure_and_post_probe_renewal_loss_cancel_without_opening(self):
        work = HELPERS.parents[1] / ".venv/tls-f30-correction-r2/unit"
        work.mkdir(parents=True, exist_ok=True)
        for phase in ("census", "reload-requested"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory(dir=work) as owned:
                directory = Path(owned)
                site = directory / "site"
                site.write_bytes(b"persistent-closed")
                binding = {
                    "phase": phase,
                    "old_workers": [],
                    "closed_sha256": hashlib.sha256(site.read_bytes()).hexdigest(),
                }
                (directory / "binding.json").write_text(json.dumps(binding))
                latest = {"minimum_valid_until": 2000, "gate_effective": True}
                (directory / "lease.json").write_text(json.dumps(latest))
                samples = [latest, ValueError("renewal unavailable")]
                with (
                    patch.object(close, "SITE", site),
                    patch.object(close, "lease", side_effect=samples),
                    patch.object(close, "workers", return_value=[]),
                    patch.object(close, "gate_closed", return_value=True),
                    patch.object(close.time, "time", return_value=1000),
                    patch.object(close, "command", side_effect=ValueError("reload failed")),
                    patch.object(drain, "stop_old_writer") as stop,
                ):
                    with self.assertRaisesRegex(ValueError, "reload failed|renewal unavailable"):
                        close.wait_closed(directory)
                    stop.assert_called_once_with(latest, emergency=True)
                assert site.read_bytes() == b"persistent-closed"
                assert json.loads((directory / "binding.json").read_text())["phase"] == "census"

    def test_renewal_loss_during_old_worker_wait_cancels_writer_and_preserves_gate(self):
        work = HELPERS.parents[1] / ".venv/tls-f30-work/wait-tests"
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=work) as owned:
            directory = Path(owned)
            (directory / "binding.json").write_text(
                json.dumps({"old_workers": [{"pid": 7, "ticks": "8"}]})
            )
            lease = {"minimum_valid_until": 2000, "gate_effective": True}
            (directory / "lease.json").write_text(json.dumps(lease))
            gate_file = directory / "closed.conf"
            gate_file.write_text("persistent-closed")
            with (
                patch.object(close, "lease", side_effect=ValueError("renewal unavailable")),
                patch.object(drain, "stop_old_writer") as stop,
            ):
                with self.assertRaisesRegex(ValueError, "renewal unavailable"):
                    close.wait_closed(directory)
                stop.assert_called_once_with(lease, emergency=True)
            assert gate_file.read_text() == "persistent-closed"


class PackageContract(unittest.TestCase):
    def test_unavailable_consumer_evidence_refuses(self):
        with (
            patch.object(Path, "iterdir", return_value=iter([Path("/proc/999999")])),
            patch.object(Path, "read_bytes", side_effect=PermissionError),
            self.assertRaisesRegex(ValueError, "cannot attest"),
        ):
            packages.stopped(Path("/fixture/venv"))
        with (
            patch.object(Path, "iterdir", return_value=iter([Path("/proc/999999")])),
            patch.object(Path, "read_bytes", side_effect=FileNotFoundError),
            patch.object(Path, "exists", return_value=True),
            self.assertRaisesRegex(ValueError, "cannot attest"),
        ):
            packages.stopped(Path("/fixture/venv"))

    def test_complete_delta_refuses_added_pip_and_unrelated_upgrade(self):
        before = {
            "prefix": "/fixture",
            "base_prefix": "/usr",
            "real_executable": "/usr/bin/python",
            "version": [3, 12, 3],
            "packages": {"certifi": "old", "sentinel": "1"},
        }
        after = copy.deepcopy(before)
        after["packages"]["certifi"] = "2026.10.19"
        packages.compare(before, after, ["certifi"])
        after["packages"]["pip"] = "26.2.2"
        with self.assertRaises(ValueError):
            packages.compare(before, after, ["certifi"])
        with self.assertRaises(ValueError):
            packages.compare(before, after, ["pip", "certifi"])
        after["packages"].pop("pip")
        after["packages"]["sentinel"] = "2"
        with self.assertRaises(ValueError):
            packages.compare(before, after, ["certifi"])

    def test_apt_only_fixed_ca(self):
        good = "1 upgraded, 0 newly installed, 0 to remove\nInst ca-certificates [old] (20260816ubuntu0.24.04.1 local-deb [all])\nConf ca-certificates (20260816ubuntu0.24.04.1 local-deb [all])\n"
        packages.apt_simulation(good)
        for bad in [
            "",
            good + "Remv openssl [3]\n",
            good + "Inst python3 [old] (new)\n",
            good.replace("20260816ubuntu0.24.04.1", "old"),
        ]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                packages.apt_simulation(bad)


if __name__ == "__main__":
    unittest.main()
