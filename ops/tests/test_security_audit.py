"""Unit tests for .github/scripts/security-audit.py and .github/security-exceptions.json."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / ".github/scripts/security-audit.py"
EXCEPTIONS_FILE = ROOT / ".github/security-exceptions.json"


def load_helper_module() -> Any:
    spec = importlib.util.spec_from_file_location("security_audit_helper", HELPER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SecurityAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.work = Path(self.temp_dir.name)
        self.summary_file = self.work / "step-summary.md"

    def write_json(self, name: str, payload: Any) -> Path:
        path = self.work / name
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    def run_cli(
        self,
        *args: str,
        stdin_text: str | None = None,
        extra_env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "GITHUB_STEP_SUMMARY": str(self.summary_file),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if extra_env is not None:
            env.update(extra_env)
        return subprocess.run(  # noqa: S603 - fixed helper CLI vector in tests
            [sys.executable, "-B", str(HELPER), *args],
            input=stdin_text,
            cwd=cwd or ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def test_checked_in_exceptions_file_conforms_to_schema(self) -> None:
        """Property (a): checked-in .github/security-exceptions.json conforms to schema."""
        helper = load_helper_module()
        entries, errors, schema_valid = helper.load_exceptions(EXCEPTIONS_FILE, today=None)
        assert schema_valid, f"schema validation failed: {errors}"
        assert errors == []

        seen: set[tuple[str, str, str]] = set()
        for entry in entries:
            normalized_pkg = (
                helper.normalize_pep503(entry.package)
                if entry.target in helper.PIP_TARGETS
                else entry.package
            )
            key = (entry.target, normalized_pkg, entry.id)
            assert key not in seen, f"duplicate exception entry: {key}"
            seen.add(key)

    def test_pip_alias_deduplication_and_active_exception_pass(self) -> None:
        """Property (b): pip PYSEC/CVE/GHSA alias deduplication and active exception exit 0."""
        scanner_path = self.write_json(
            "pip-dup.json",
            {
                "dependencies": [
                    {
                        "name": "Pydantic_Settings",
                        "version": "2.12.0",
                        "vulns": [
                            {
                                "id": "PYSEC-2026-2104",
                                "fix_versions": ["2.14.2"],
                                "aliases": ["CVE-2026-34993"],
                            },
                            {
                                "id": "GHSA-jg22-mg44-37j8",
                                "fix_versions": ["2.14.2"],
                                "aliases": ["CVE-2026-34993", "PYSEC-2026-2104"],
                            },
                        ],
                    }
                ],
                "fixes": [],
            },
        )
        exceptions_path = self.write_json(
            "exceptions-active.json",
            {
                "exceptions": [
                    {
                        "id": "GHSA-jg22-mg44-37j8",
                        "target": "packages/bot/requirements.txt",
                        "package": "pydantic.settings",
                        "reason": "Polling only over verified TLS.",
                        "decision_ref": "2026-11-18: temporary hold",
                        "expires": "2026-11-30",
                    }
                ]
            },
        )

        # Boundary: today == expires (2026-11-30) is still active and exits 0.
        result = self.run_cli(
            "pip",
            "--target",
            "packages/bot/requirements.txt",
            "--exceptions",
            str(exceptions_path),
            "--today",
            "2026-11-30",
            "--scanner-json",
            str(scanner_path),
            "--scanner-exit-code",
            "1",
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Active temporary exceptions (1):" in result.stdout
        assert "Unapproved vulnerabilities" not in result.stdout
        assert self.summary_file.read_text(encoding="utf-8") == result.stdout

    def test_uncovered_or_wrong_target_or_package_exception_fails(self) -> None:
        """Property (c): uncovered finding or wrong target/package exception exits 1."""
        scanner_path = self.write_json(
            "pip-one.json",
            {
                "dependencies": [
                    {
                        "name": "aiohttp",
                        "version": "3.13.4",
                        "vulns": [
                            {
                                "id": "PYSEC-2026-9999",
                                "fix_versions": ["3.14.4"],
                                "aliases": ["GHSA-jm3w-gw2p-xjfj"],
                            }
                        ],
                    }
                ],
                "fixes": [],
            },
        )
        cases = [
            ("uncovered", []),
            (
                "wrong_target",
                [
                    {
                        "id": "GHSA-jm3w-gw2p-xjfj",
                        "target": "packages/api/requirements.txt",
                        "package": "aiohttp",
                        "reason": "Wrong target.",
                        "decision_ref": "fixture",
                        "expires": "2026-11-30",
                    }
                ],
            ),
            (
                "wrong_package",
                [
                    {
                        "id": "GHSA-jm3w-gw2p-xjfj",
                        "target": "packages/bot/requirements.txt",
                        "package": "yarl",
                        "reason": "Wrong package.",
                        "decision_ref": "fixture",
                        "expires": "2026-11-30",
                    }
                ],
            ),
        ]
        for label, entries in cases:
            with self.subTest(case=label):
                exc_path = self.write_json(f"exc-{label}.json", {"exceptions": entries})
                result = self.run_cli(
                    "pip",
                    "--target",
                    "packages/bot/requirements.txt",
                    "--exceptions",
                    str(exc_path),
                    "--today",
                    "2026-11-19",
                    "--scanner-json",
                    str(scanner_path),
                    "--scanner-exit-code",
                    "1",
                )
                assert result.returncode == 1
                assert "Unapproved vulnerabilities (1):" in result.stdout
                assert "GHSA-jm3w-gw2p-xjfj" in result.stdout

    def test_expired_exception_and_invalid_schema_fail_closed_globally(self) -> None:
        """Property (d): expired exception (expires < today) exits 1 even on a clean target."""
        clean_api_scan = self.write_json("pip-clean.json", {"dependencies": [], "fixes": []})
        bot_exc = self.write_json(
            "exc-bot-expired.json",
            {
                "exceptions": [
                    {
                        "id": "GHSA-jg22-mg44-37j8",
                        "target": "packages/bot/requirements.txt",
                        "package": "aiohttp",
                        "reason": "Polling only.",
                        "decision_ref": "fixture",
                        "expires": "2026-11-30",
                    }
                ]
            },
        )
        expired = self.run_cli(
            "pip",
            "--target",
            "packages/api/requirements.txt",
            "--exceptions",
            str(bot_exc),
            "--today",
            "2026-12-01",
            "--scanner-json",
            str(clean_api_scan),
        )
        assert expired.returncode == 1
        assert "Gate errors (fail-closed):" in expired.stdout
        assert "expired 2026-11-30 < 2026-12-01" in expired.stdout

        invalid_payloads: list[tuple[str, Any]] = [
            ("extra_top_key", {"exceptions": [], "extra": 1}),
            (
                "missing_field",
                {
                    "exceptions": [
                        {
                            "id": "GHSA-jg22-mg44-37j8",
                            "target": "packages/api/requirements.txt",
                            "package": "pkg",
                            "reason": "r",
                            "expires": "2026-11-30",
                        }
                    ]
                },
            ),
            (
                "compact_non_hyphen_date",
                {
                    "exceptions": [
                        {
                            "id": "GHSA-jg22-mg44-37j8",
                            "target": "packages/api/requirements.txt",
                            "package": "pkg",
                            "reason": "r",
                            "decision_ref": "ref",
                            "expires": "20261130",
                        }
                    ]
                },
            ),
            (
                "invalid_target",
                {
                    "exceptions": [
                        {
                            "id": "GHSA-jg22-mg44-37j8",
                            "target": "packages/unknown/requirements.txt",
                            "package": "pkg",
                            "reason": "r",
                            "decision_ref": "ref",
                            "expires": "2026-11-30",
                        }
                    ]
                },
            ),
        ]
        for label, bad_payload in invalid_payloads:
            with self.subTest(schema_case=label):
                bad_path = self.write_json(f"bad-{label}.json", bad_payload)
                res = self.run_cli(
                    "pip",
                    "--target",
                    "packages/api/requirements.txt",
                    "--exceptions",
                    str(bad_path),
                    "--today",
                    "2026-11-19",
                    "--scanner-json",
                    str(clean_api_scan),
                )
                assert res.returncode == 1
                assert "Gate errors (fail-closed):" in res.stdout

        missing_res = self.run_cli(
            "pip",
            "--target",
            "packages/api/requirements.txt",
            "--exceptions",
            str(self.work / "does-not-exist.json"),
            "--today",
            "2026-11-19",
            "--scanner-json",
            str(clean_api_scan),
        )
        assert missing_res.returncode == 1

    def test_malformed_or_error_scanner_output_fails_closed(self) -> None:
        """Property (e): non-JSON, malformed, or rc!=0 empty scanner output fails closed."""
        empty_exc = self.write_json("empty-exc.json", {"exceptions": []})

        non_json = self.work / "corrupt.json"
        non_json.write_text("{not valid json", encoding="utf-8")

        pip_skipped = self.write_json(
            "pip-skipped.json",
            {"dependencies": [{"name": "local-pkg", "skip_reason": "not on PyPI"}]},
        )
        pip_empty = self.write_json("pip-empty.json", {"dependencies": [], "fixes": []})
        npm_err_obj = self.write_json(
            "npm-error.json",
            {"error": {"code": "ENOAUDIT", "summary": "registry unreachable"}},
        )
        npm_wrong_ver = self.write_json(
            "npm-v1.json",
            {"auditReportVersion": 1, "vulnerabilities": {}},
        )
        npm_no_id = self.write_json(
            "npm-no-id.json",
            {
                "auditReportVersion": 2,
                "vulnerabilities": {
                    "zod": {
                        "name": "zod",
                        "severity": "high",
                        "via": [{"severity": "high", "title": "missing identifiers"}],
                        "range": "<4.6.6",
                        "fixAvailable": True,
                    }
                },
            },
        )
        npm_empty = self.write_json(
            "npm-empty.json",
            {"auditReportVersion": 2, "vulnerabilities": {}},
        )

        cases = [
            ("pip", "packages/api/requirements.txt", non_json, 1),
            ("pip", "packages/api/requirements.txt", pip_skipped, 0),
            ("pip", "packages/api/requirements.txt", pip_empty, 1),
            ("npm", "packages/frontend", non_json, 1),
            ("npm", "packages/frontend", npm_err_obj, 1),
            ("npm", "packages/frontend", npm_wrong_ver, 0),
            ("npm", "packages/frontend", npm_no_id, 1),
            ("npm", "packages/frontend", npm_empty, 1),
        ]
        for mode, target, scan_file, rc in cases:
            with self.subTest(mode=mode, scan_file=scan_file.name, rc=rc):
                res = self.run_cli(
                    mode,
                    "--target",
                    target,
                    "--exceptions",
                    str(empty_exc),
                    "--today",
                    "2026-11-19",
                    "--scanner-json",
                    str(scan_file),
                    "--scanner-exit-code",
                    str(rc),
                )
                assert res.returncode == 1
                assert "Gate errors (fail-closed):" in res.stdout

        # Also exercise live invoke_scanner failure when npm binary exits 1 with invalid JSON.
        fake_bin = self.work / "fake-bin"
        fake_bin.mkdir()
        fake_npm = fake_bin / "npm"
        fake_npm.write_text("#!/bin/sh\necho 'broken stdout'\nexit 1\n", encoding="utf-8")
        fake_npm.chmod(0o755)
        fake_target = self.work / "packages/frontend"
        fake_target.mkdir(parents=True)
        live_res = self.run_cli(
            "npm",
            "--target",
            "packages/frontend",
            "--exceptions",
            str(empty_exc),
            "--today",
            "2026-11-19",
            cwd=self.work,
            extra_env={"PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"},
        )
        assert live_res.returncode == 1
        assert "Gate errors (fail-closed):" in live_res.stdout

    def test_npm_transitive_via_and_moderate_ignored_at_high_threshold(self) -> None:
        """Property (f): npm transitive via chains and moderate advisories under high threshold."""
        npm_report = self.write_json(
            "npm-transitive.json",
            {
                "auditReportVersion": 2,
                "vulnerabilities": {
                    "follow-redirects": {
                        "name": "follow-redirects",
                        "severity": "moderate",
                        "via": [
                            {
                                "source": 1100001,
                                "name": "follow-redirects",
                                "severity": "moderate",
                                "url": "https://github.com/advisories/GHSA-r4q5-vmmm-2653",
                                "range": "<=1.15.11",
                            }
                        ],
                        "range": "<=1.15.11",
                        "fixAvailable": True,
                    },
                    "react-router": {
                        "name": "react-router",
                        "severity": "high",
                        "via": [
                            {
                                "source": 1100002,
                                "name": "react-router",
                                "severity": "high",
                                "url": "https://github.com/advisories/GHSA-2w69-qvjg-hvjx",
                                "range": ">=7.0.0 <=7.17.0",
                            }
                        ],
                        "range": ">=7.0.0 <=7.17.0",
                        "fixAvailable": {
                            "name": "react-router-dom",
                            "version": "7.18.5",
                            "isSemVerMajor": False,
                        },
                    },
                    "react-router-dom": {
                        "name": "react-router-dom",
                        "severity": "high",
                        "via": ["react-router"],
                        "range": ">=7.0.0 <=7.17.0",
                        "fixAvailable": {
                            "name": "react-router-dom",
                            "version": "7.18.5",
                            "isSemVerMajor": False,
                        },
                    },
                },
            },
        )
        empty_exc = self.write_json("npm-empty-exc.json", {"exceptions": []})
        uncovered_res = self.run_cli(
            "npm",
            "--target",
            "packages/frontend",
            "--exceptions",
            str(empty_exc),
            "--today",
            "2026-11-19",
            "--scanner-json",
            str(npm_report),
            "--scanner-exit-code",
            "1",
        )
        assert uncovered_res.returncode == 1
        assert "Unapproved vulnerabilities (1):" in uncovered_res.stdout
        assert "GHSA-2w69-qvjg-hvjx" in uncovered_res.stdout
        assert "fix: `react-router-dom@7.18.5`" in uncovered_res.stdout
        assert "follow-redirects" not in uncovered_res.stdout

        covered_exc = self.write_json(
            "npm-covered-exc.json",
            {
                "exceptions": [
                    {
                        "id": "GHSA-2w69-qvjg-hvjx",
                        "target": "packages/frontend",
                        "package": "react-router",
                        "reason": "Client router advisory covered in test.",
                        "decision_ref": "fixture",
                        "expires": "2026-11-30",
                    }
                ]
            },
        )
        covered_res = self.run_cli(
            "npm",
            "--target",
            "packages/frontend",
            "--exceptions",
            str(covered_exc),
            "--today",
            "2026-11-19",
            "--scanner-json",
            str(npm_report),
            "--scanner-exit-code",
            "1",
        )
        assert covered_res.returncode == 0, covered_res.stdout
        assert "Active temporary exceptions (1):" in covered_res.stdout

        # Orphan cyclic high package node with no direct high/critical advisory fails closed.
        orphan_report = self.write_json(
            "npm-orphan.json",
            {
                "auditReportVersion": 2,
                "vulnerabilities": {
                    "pkg-a": {"name": "pkg-a", "severity": "high", "via": ["pkg-b"]},
                    "pkg-b": {"name": "pkg-b", "severity": "high", "via": ["pkg-a"]},
                },
            },
        )
        orphan_res = self.run_cli(
            "npm",
            "--target",
            "packages/frontend",
            "--exceptions",
            str(empty_exc),
            "--today",
            "2026-11-19",
            "--scanner-json",
            str(orphan_report),
            "--scanner-exit-code",
            "1",
        )
        assert orphan_res.returncode == 1
        assert "Gate errors (fail-closed):" in orphan_res.stdout

    def test_scope_routes_packages_and_fails_closed(self) -> None:
        """Property (g): scope routes package paths and fails closed to all four targets."""
        all_four = ["admin", "api", "bot", "frontend"]
        cases: list[tuple[str, str, list[str], bool]] = [
            ("api_only", "packages/api/requirements.txt\n", ["api"], False),
            ("bot_only", "packages/bot/bot/main.py\n", ["bot"], False),
            ("frontend_only", "packages/frontend/package.json\n", ["frontend"], False),
            ("admin_only", "admin-panel-v2/package-lock.json\n", ["admin"], False),
            (
                "non_audited_metadata",
                "README.md\ndocs/guide.md\nops/shop-release-assets.py\n"
                ".github/workflows/ci.yml\n.github/scripts/ci-changes.py\n.gitleaks.toml\n",
                [],
                False,
            ),
            ("security_workflow", ".github/workflows/security.yml\n", all_four, False),
            ("security_helper", ".github/scripts/security-audit.py\n", all_four, False),
            ("security_exceptions", ".github/security-exceptions.json\n", all_four, False),
            ("unclassified_path", "Makefile\n", all_four, False),
            ("empty_stdin", "", all_four, True),
        ]

        for label, stdin_paths, expected_targets, expect_warning in cases:
            with self.subTest(scope_case=label):
                gh_out = self.work / f"gh-out-{label}.txt"
                res = self.run_cli(
                    "scope",
                    "--paths-stdin",
                    stdin_text=stdin_paths,
                    extra_env={"GITHUB_OUTPUT": str(gh_out)},
                )
                assert res.returncode == 0, res.stderr
                lines = res.stdout.strip().splitlines()
                if expect_warning:
                    assert lines[0].startswith("::warning::Security runs all audits:")
                    report = json.loads(lines[1])
                else:
                    report = json.loads(lines[0])
                assert report["targets"] == expected_targets

                written = dict(
                    line.split("=", 1)
                    for line in gh_out.read_text(encoding="utf-8").strip().splitlines()
                )
                assert written == {
                    key: ("true" if key in expected_targets else "false")
                    for key in ("api", "bot", "frontend", "admin")
                }

        # Non-merge single-parent commit or invalid ref fails closed to all four targets.
        bad_git_res = self.run_cli("scope", "--head", "not-a-valid-git-revision")
        assert bad_git_res.returncode == 0
        assert "::warning::Security runs all audits:" in bad_git_res.stdout
        assert json.loads(bad_git_res.stdout.strip().splitlines()[-1])["targets"] == all_four

        # Real two-parent merge commit in an isolated temporary repository selects only touched target.
        repo = self.work / "mini-repo"
        repo.mkdir()
        git_env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "test",
            "GIT_AUTHOR_EMAIL": "test@users.noreply.github.com",
            "GIT_COMMITTER_NAME": "test",
            "GIT_COMMITTER_EMAIL": "test@users.noreply.github.com",
        }
        for cmd in (
            ["git", "init", "-b", "main"],
            ["git", "commit", "--allow-empty", "-m", "base"],
            ["git", "checkout", "-b", "feat"],
        ):
            subprocess.run(cmd, cwd=repo, env=git_env, check=True, capture_output=True)  # noqa: S603
        (repo / "packages/bot").mkdir(parents=True)
        (repo / "packages/bot/requirements.txt").write_text("aiohttp==3.14.5\n", encoding="utf-8")
        for cmd in (
            ["git", "add", "packages/bot/requirements.txt"],
            ["git", "commit", "-m", "change bot"],
            ["git", "checkout", "main"],
            ["git", "merge", "--no-ff", "feat", "-m", "merge pr"],
        ):
            subprocess.run(cmd, cwd=repo, env=git_env, check=True, capture_output=True)  # noqa: S603
        merge_res = self.run_cli("scope", "--head", "HEAD", cwd=repo)
        assert merge_res.returncode == 0
        assert json.loads(merge_res.stdout.strip())["targets"] == ["bot"]

        # Single-parent commit on feat branch fails closed to all four targets.
        single_parent_res = self.run_cli("scope", "--head", "feat", cwd=repo)
        assert single_parent_res.returncode == 0
        assert "::warning::Security runs all audits:" in single_parent_res.stdout
        assert json.loads(single_parent_res.stdout.strip().splitlines()[-1])["targets"] == all_four


if __name__ == "__main__":
    unittest.main()
