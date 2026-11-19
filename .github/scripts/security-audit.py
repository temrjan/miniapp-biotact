"""Run blocking Python and Node dependency audits with expiring exceptions.

Subcommands:
  pip    Run pip-audit in JSON mode against a requirements file and check exceptions.
  npm    Run npm audit in JSON mode (--audit-level=high --omit=dev) and check exceptions.
  scope  Select which dependency audit jobs a pull request merge commit touches.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PIP_TARGETS = frozenset(
    {
        "packages/api/requirements.txt",
        "packages/bot/requirements.txt",
    }
)
NPM_TARGETS = frozenset(
    {
        "packages/frontend",
        "admin-panel-v2",
    }
)
VALID_TARGETS = PIP_TARGETS | NPM_TARGETS
REQUIRED_EXCEPTION_KEYS = frozenset(
    {"id", "target", "package", "reason", "decision_ref", "expires"}
)

SECURITY_TARGETS = ("api", "bot", "frontend", "admin")
ALL_SCOPE_TARGETS = frozenset(SECURITY_TARGETS)
NO_SCOPE_TARGETS: frozenset[str] = frozenset()
HIGH_SEVERITIES = frozenset({"high", "critical"})

SCOPE_PREFIX_RULES: tuple[tuple[str, frozenset[str]], ...] = (
    ("packages/api/", frozenset({"api"})),
    ("packages/bot/", frozenset({"bot"})),
    ("packages/frontend/", frozenset({"frontend"})),
    ("admin-panel-v2/", frozenset({"admin"})),
    ("docs/", NO_SCOPE_TARGETS),
    (".claude/", NO_SCOPE_TARGETS),
    (".kimi/", NO_SCOPE_TARGETS),
    ("ops/", NO_SCOPE_TARGETS),
)
SCOPE_EXACT_NO_TARGETS = frozenset(
    {
        ".github/workflows/ci.yml",
        ".github/workflows/deploy.yml",
        ".github/scripts/ci-changes.py",
        ".gitleaks.toml",
    }
)
SCOPE_EXACT_ALL_TARGETS = frozenset(
    {
        ".github/workflows/security.yml",
        ".github/scripts/security-audit.py",
        ".github/security-exceptions.json",
    }
)

ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
GHSA_RE = re.compile(r"GHSA-[0-9a-zA-Z]{4}-[0-9a-zA-Z]{4}-[0-9a-zA-Z]{4}")


@dataclass(frozen=True)
class ExceptionEntry:
    id: str
    target: str
    package: str
    reason: str
    decision_ref: str
    expires: str
    expires_date: datetime.date


@dataclass(frozen=True)
class Finding:
    package: str
    version: str
    canonical_id: str
    ids: frozenset[str]
    fix: str


def normalize_pep503(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_iso_date(value: str) -> datetime.date:
    if not ISO_DATE_RE.fullmatch(value):
        raise ValueError(f"expected YYYY-MM-DD date, got {value!r}")
    return datetime.date.fromisoformat(value)


def choose_canonical_id(ids: Iterable[str]) -> str:
    ghsas = sorted(item for item in ids if item.startswith("GHSA-"))
    if ghsas:
        return ghsas[0]
    cves = sorted(item for item in ids if item.startswith("CVE-"))
    if cves:
        return cves[0]
    return sorted(ids)[0]


def load_exceptions(
    path: Path, today: datetime.date | None
) -> tuple[list[ExceptionEntry], list[str], bool]:
    """Load and validate the exceptions file.

    Returns ``(active_entries, gate_errors, schema_valid)``. When ``today`` is given,
    any entry with ``expires < today`` appends a gate error so the caller fails closed
    regardless of which target is audited.
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as err:
        return [], [f"cannot read exceptions file {path}: {err}"], False

    try:
        payload: Any = json.loads(raw_text)
    except json.JSONDecodeError as err:
        return [], [f"exceptions file {path} is not valid JSON: {err}"], False

    if not isinstance(payload, dict) or set(payload.keys()) != {"exceptions"}:
        return [], [f"exceptions file {path} must contain exact top-level key 'exceptions'"], False

    raw_items = payload.get("exceptions")
    if not isinstance(raw_items, list):
        return [], [f"exceptions file {path} key 'exceptions' must be a JSON array"], False

    parsed: list[ExceptionEntry] = []
    schema_errors: list[str] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            schema_errors.append(f"exceptions[{index}] must be an object")
            continue
        keys = set(item.keys())
        if keys != REQUIRED_EXCEPTION_KEYS:
            missing = sorted(REQUIRED_EXCEPTION_KEYS - keys)
            extra = sorted(keys - REQUIRED_EXCEPTION_KEYS)
            schema_errors.append(
                f"exceptions[{index}] invalid keys (missing={missing}, extra={extra})"
            )
            continue
        bad_fields = [
            key
            for key in sorted(REQUIRED_EXCEPTION_KEYS)
            if not isinstance(item[key], str) or not item[key].strip()
        ]
        if bad_fields:
            schema_errors.append(
                f"exceptions[{index}] must have non-empty string values for {bad_fields}"
            )
            continue
        target = item["target"]
        if target not in VALID_TARGETS:
            schema_errors.append(f"exceptions[{index}] invalid target {target!r}")
            continue
        expires_str = item["expires"]
        try:
            expires_date = parse_iso_date(expires_str)
        except ValueError as err:
            schema_errors.append(f"exceptions[{index}] invalid expires {expires_str!r}: {err}")
            continue
        parsed.append(
            ExceptionEntry(
                id=item["id"],
                target=target,
                package=item["package"],
                reason=item["reason"],
                decision_ref=item["decision_ref"],
                expires=expires_str,
                expires_date=expires_date,
            )
        )

    if schema_errors:
        return [], schema_errors, False

    if today is None:
        return parsed, [], True

    active: list[ExceptionEntry] = []
    expiry_errors: list[str] = []
    for entry in parsed:
        if entry.expires_date < today:
            expiry_errors.append(
                f"expired exception {entry.id} ({entry.package} in {entry.target}): "
                f"expired {entry.expires} < {today.isoformat()}"
            )
        else:
            active.append(entry)
    return active, expiry_errors, True


def merge_id_groups(
    existing: list[tuple[set[str], set[str]]], ids: set[str], fixes: set[str]
) -> list[tuple[set[str], set[str]]]:
    merged_ids = set(ids)
    merged_fixes = set(fixes)
    kept: list[tuple[set[str], set[str]]] = []
    for group_ids, group_fixes in existing:
        if group_ids & merged_ids:
            merged_ids |= group_ids
            merged_fixes |= group_fixes
        else:
            kept.append((group_ids, group_fixes))
    kept.append((merged_ids, merged_fixes))
    return kept


def format_stderr_suffix(stderr: str) -> str:
    cleaned = stderr.strip()
    return f" | stderr: {cleaned}" if cleaned else ""


def parse_pip_output(stdout: str, stderr: str, returncode: int) -> tuple[list[Finding], list[str]]:
    try:
        payload: Any = json.loads(stdout)
    except json.JSONDecodeError as err:
        return (
            [],
            [
                f"pip-audit did not return valid JSON (rc={returncode}): "
                f"{err}{format_stderr_suffix(stderr)}"
            ],
        )

    if not isinstance(payload, dict) or not isinstance(payload.get("dependencies"), list):
        return (
            [],
            [
                f"pip-audit returned invalid report shape (rc={returncode}){format_stderr_suffix(stderr)}"
            ],
        )

    errors: list[str] = []
    grouped: dict[tuple[str, str], tuple[str, list[tuple[set[str], set[str]]]]] = {}
    for index, dep in enumerate(payload["dependencies"]):
        if not isinstance(dep, dict) or "skip_reason" in dep:
            errors.append(f"pip-audit dependency[{index}] was skipped or malformed")
            continue
        name = dep.get("name")
        version = dep.get("version")
        vulns = dep.get("vulns")
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(version, str)
            or not version.strip()
            or not isinstance(vulns, list)
        ):
            errors.append(f"pip-audit dependency[{index}] missing valid name, version, or vulns")
            continue
        key = (normalize_pep503(name), version)
        display_name, groups = grouped.get(key, (name, []))
        for vuln_index, vuln in enumerate(vulns):
            if not isinstance(vuln, dict):
                errors.append(f"pip-audit vuln[{vuln_index}] in {name} is not an object")
                continue
            vuln_id = vuln.get("id")
            aliases = vuln.get("aliases", [])
            fix_versions = vuln.get("fix_versions", [])
            if (
                not isinstance(vuln_id, str)
                or not vuln_id.strip()
                or not isinstance(aliases, list)
                or any(not isinstance(alias, str) or not alias.strip() for alias in aliases)
                or not isinstance(fix_versions, list)
                or any(not isinstance(ver, str) for ver in fix_versions)
            ):
                errors.append(f"pip-audit vuln[{vuln_index}] in {name} has malformed fields")
                continue
            ids = {vuln_id.strip(), *(alias.strip() for alias in aliases)}
            fixes = {ver.strip() for ver in fix_versions if ver.strip()}
            groups = merge_id_groups(groups, ids, fixes)
        if groups:
            grouped[key] = (display_name, groups)

    findings: list[Finding] = []
    for (_, version), (display_name, groups) in grouped.items():
        for group_ids, group_fixes in groups:
            findings.append(
                Finding(
                    package=display_name,
                    version=version,
                    canonical_id=choose_canonical_id(group_ids),
                    ids=frozenset(group_ids),
                    fix=", ".join(sorted(group_fixes)) if group_fixes else "none",
                )
            )

    if returncode != 0 and not findings and not errors:
        errors.append(
            f"pip-audit exited with {returncode} without reporting vulnerability entries"
            f"{format_stderr_suffix(stderr)}"
        )
    return findings, errors


def extract_npm_fix(pkg_name: str, fix_available: Any) -> str:
    if isinstance(fix_available, dict):
        fix_name = fix_available.get("name")
        fix_version = fix_available.get("version")
        if isinstance(fix_name, str) and isinstance(fix_version, str) and fix_version.strip():
            if fix_name == pkg_name:
                return fix_version.strip()
            return f"{fix_name}@{fix_version.strip()}"
    if fix_available is True:
        return "available"
    return "none"


def extract_ghsa_id(item: dict[str, Any]) -> str | None:
    for field in ("url", "id"):
        raw = item.get(field)
        if isinstance(raw, str):
            match = GHSA_RE.search(raw)
            if match is not None:
                matched = match.group(0)
                return "GHSA-" + matched[5:].lower()
    return None


def extract_source_id(item: dict[str, Any]) -> str | None:
    source = item.get("source")
    if source is None or isinstance(source, bool):
        return None
    if isinstance(source, int):
        return str(source)
    if isinstance(source, str) and source.strip():
        return source.strip()
    return None


def resolves_to_direct_advisory(
    start: str, via_edges: dict[str, set[str]], direct_packages: set[str]
) -> bool:
    stack = [start]
    visited: set[str] = set()
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        if current in direct_packages:
            return True
        stack.extend(via_edges.get(current, set()) - visited)
    return False


def parse_npm_output(stdout: str, stderr: str, returncode: int) -> tuple[list[Finding], list[str]]:
    try:
        payload: Any = json.loads(stdout)
    except json.JSONDecodeError as err:
        return (
            [],
            [
                f"npm audit returned invalid or error report (rc={returncode}): "
                f"{err}{format_stderr_suffix(stderr)}"
            ],
        )

    if (
        not isinstance(payload, dict)
        or "error" in payload
        or payload.get("auditReportVersion") != 2
        or not isinstance(payload.get("vulnerabilities"), dict)
    ):
        return (
            [],
            [
                f"npm audit returned invalid or error report (rc={returncode})"
                f"{format_stderr_suffix(stderr)}"
            ],
        )

    errors: list[str] = []
    pkg_severities: dict[str, str] = {}
    via_edges: dict[str, set[str]] = {}
    direct_by_pkg: dict[str, tuple[str, str, list[tuple[set[str], set[str]]]]] = {}

    for pkg_name, info in payload["vulnerabilities"].items():
        if (
            not isinstance(pkg_name, str)
            or not pkg_name.strip()
            or not isinstance(info, dict)
            or not isinstance(info.get("severity"), str)
            or not isinstance(info.get("via"), list)
        ):
            errors.append(f"npm vulnerability node {pkg_name!r} has malformed fields")
            continue

        pkg_severities[pkg_name] = info["severity"]
        via_edges.setdefault(pkg_name, set())
        pkg_range = info.get("range")
        default_range = (
            pkg_range.strip() if isinstance(pkg_range, str) and pkg_range.strip() else "unknown"
        )
        fix_str = extract_npm_fix(pkg_name, info.get("fixAvailable"))

        for via_index, item in enumerate(info["via"]):
            if isinstance(item, str):
                if not item.strip():
                    errors.append(f"npm package {pkg_name!r} has empty string in via[{via_index}]")
                else:
                    via_edges[pkg_name].add(item.strip())
                continue
            if not isinstance(item, dict) or not isinstance(item.get("severity"), str):
                errors.append(f"npm package {pkg_name!r} has malformed via[{via_index}] entry")
                continue
            if item["severity"] not in HIGH_SEVERITIES:
                continue

            ghsa_id = extract_ghsa_id(item)
            source_id = extract_source_id(item)
            ids = {val for val in (ghsa_id, source_id) if val is not None}
            if not ids:
                errors.append(
                    f"npm high/critical advisory in {pkg_name!r} lacks both GHSA and source ID"
                )
                continue

            item_range = item.get("range")
            version_str = (
                item_range.strip()
                if isinstance(item_range, str) and item_range.strip()
                else default_range
            )
            stored_range, stored_fix, groups = direct_by_pkg.get(
                pkg_name, (version_str, fix_str, [])
            )
            fixes = {stored_fix} if stored_fix != "none" else set()
            groups = merge_id_groups(groups, ids, fixes)
            direct_by_pkg[pkg_name] = (stored_range, stored_fix, groups)

    direct_packages = set(direct_by_pkg.keys())
    for pkg_name, severity in pkg_severities.items():
        if severity in HIGH_SEVERITIES and not resolves_to_direct_advisory(
            pkg_name, via_edges, direct_packages
        ):
            errors.append(
                f"npm package {pkg_name!r} has severity {severity!r} without a resolvable "
                "direct high/critical advisory"
            )

    findings: list[Finding] = []
    for pkg_name, (version_str, _, groups) in direct_by_pkg.items():
        for group_ids, group_fixes in groups:
            findings.append(
                Finding(
                    package=pkg_name,
                    version=version_str,
                    canonical_id=choose_canonical_id(group_ids),
                    ids=frozenset(group_ids),
                    fix=", ".join(sorted(group_fixes)) if group_fixes else "none",
                )
            )

    if returncode != 0 and not findings and not errors:
        errors.append(
            f"npm audit exited with {returncode} without reporting high/critical vulnerability entries"
            f"{format_stderr_suffix(stderr)}"
        )
    return findings, errors


def match_exception(
    mode: str, target: str, finding: Finding, entries: list[ExceptionEntry]
) -> ExceptionEntry | None:
    for entry in entries:
        if entry.target != target:
            continue
        pkg_matches = (
            normalize_pep503(entry.package) == normalize_pep503(finding.package)
            if mode == "pip"
            else entry.package == finding.package
        )
        if pkg_matches and entry.id in finding.ids:
            return entry
    return None


def render_summary(
    mode: str,
    target: str,
    gate_errors: list[str],
    uncovered: list[Finding],
    covered: list[tuple[Finding, ExceptionEntry]],
) -> str:
    lines = [f"### Security audit (`{mode}`): `{target}`", ""]
    if not gate_errors and not uncovered and not covered:
        lines.append("0 vulnerabilities, 0 exceptions.")
        return "\n".join(lines) + "\n"

    if gate_errors:
        lines.append("**Gate errors (fail-closed):**")
        for err in gate_errors:
            lines.append(f"- `{err}`")
        lines.append("")

    if uncovered:
        lines.append(f"**Unapproved vulnerabilities ({len(uncovered)}):**")
        for finding in uncovered:
            lines.append(
                f"- `{finding.package}` (`{finding.version}`): "
                f"`{finding.canonical_id}` (fix: `{finding.fix}`)"
            )
        lines.append("")

    if covered:
        lines.append(f"**Active temporary exceptions ({len(covered)}):**")
        for finding, entry in covered:
            lines.append(
                f"- `{finding.package}` `{entry.id}` — expires `{entry.expires}` "
                f"(`{entry.decision_ref}`)"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def emit_summary(summary: str) -> bool:
    sys.stdout.write(summary)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not summary_path:
        return True
    try:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(summary)
    except OSError as err:
        sys.stderr.write(f"failed to write GITHUB_STEP_SUMMARY ({summary_path}): {err}\n")
        return False
    return True


def invoke_scanner(mode: str, target: str) -> tuple[str, str, int, str | None]:
    if mode == "pip":
        cmd = [
            sys.executable,
            "-m",
            "pip_audit",
            "--progress-spinner=off",
            "--format=json",
            "--desc",
            "--vulnerability-service=pypi",
            "--requirement",
            target,
        ]
        cwd: str | None = None
    else:
        cmd = ["npm", "audit", "--audit-level=high", "--omit=dev", "--json"]
        cwd = target

    try:
        completed = subprocess.run(  # noqa: S603 - fixed scanner argument vector
            cmd,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as err:
        return "", "", 1, f"failed to execute {mode} scanner: {err}"
    return completed.stdout, completed.stderr, completed.returncode, None


def run_audit(args: argparse.Namespace) -> int:
    mode: str = args.mode
    target: str = args.target
    expected_targets = PIP_TARGETS if mode == "pip" else NPM_TARGETS

    gate_errors: list[str] = []
    if target not in expected_targets:
        gate_errors.append(f"unsupported {mode} audit target {target!r}")

    today: datetime.date | None = None
    if args.today is not None:
        try:
            today = parse_iso_date(args.today)
        except ValueError as err:
            gate_errors.append(f"invalid --today value: {err}")
    else:
        today = datetime.datetime.now(datetime.UTC).date()

    active_entries: list[ExceptionEntry] = []
    schema_valid = False
    if today is not None:
        active_entries, exc_errors, schema_valid = load_exceptions(Path(args.exceptions), today)
        gate_errors.extend(exc_errors)

    uncovered: list[Finding] = []
    covered: list[tuple[Finding, ExceptionEntry]] = []

    if schema_valid and target in expected_targets:
        if args.scanner_json is not None:
            try:
                stdout = Path(args.scanner_json).read_text(encoding="utf-8")
                stderr = ""
                returncode = int(args.scanner_exit_code)
                exec_error: str | None = None
            except (OSError, UnicodeDecodeError) as err:
                stdout, stderr, returncode = "", "", 1
                exec_error = f"cannot read --scanner-json {args.scanner_json}: {err}"
        else:
            stdout, stderr, returncode, exec_error = invoke_scanner(mode, target)

        if exec_error is not None:
            gate_errors.append(exec_error)
        else:
            findings, parse_errors = (
                parse_pip_output(stdout, stderr, returncode)
                if mode == "pip"
                else parse_npm_output(stdout, stderr, returncode)
            )
            gate_errors.extend(parse_errors)
            for finding in findings:
                matched = match_exception(mode, target, finding, active_entries)
                if matched is not None:
                    covered.append((finding, matched))
                else:
                    uncovered.append(finding)

    summary = render_summary(mode, target, gate_errors, uncovered, covered)
    wrote_summary = emit_summary(summary)
    if not wrote_summary or gate_errors or uncovered:
        return 1
    return 0


def scope_targets_for_path(path: str) -> frozenset[str] | None:
    if path in SCOPE_EXACT_ALL_TARGETS:
        return ALL_SCOPE_TARGETS
    if path in SCOPE_EXACT_NO_TARGETS:
        return NO_SCOPE_TARGETS
    for prefix, targets in SCOPE_PREFIX_RULES:
        if path.startswith(prefix):
            return targets
    if "/" not in path and path.endswith(".md"):
        return NO_SCOPE_TARGETS
    return None


def classify_scope_paths(paths: Iterable[str]) -> tuple[frozenset[str], list[str]]:
    selected: set[str] = set()
    unknown: list[str] = []
    for path in paths:
        targets = scope_targets_for_path(path)
        if targets is None:
            unknown.append(path)
        else:
            selected |= targets
    return (ALL_SCOPE_TARGETS if unknown else frozenset(selected)), unknown


def git_stdout(*args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed git argument vector
        ["git", *args],  # noqa: S607 - git from runner PATH
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def resolve_pr_changed_files(head_ref: str) -> tuple[str, str, list[str]]:
    head = git_stdout("rev-parse", "--verify", "--end-of-options", f"{head_ref}^{{commit}}").strip()
    parents = git_stdout("rev-list", "--parents", "-n", "1", head).split()[1:]
    if len(parents) != 2:
        raise ValueError(f"{head} is not a two-parent merge commit")
    base = parents[0]
    diff_out = git_stdout("diff", "--name-only", "--no-renames", "-z", base, head, "--")
    paths = [entry for entry in diff_out.split("\0") if entry]
    return base, head, paths


def run_scope(args: argparse.Namespace) -> int:
    report: dict[str, object] = {}
    selected: frozenset[str]
    try:
        if args.paths_stdin:
            paths = [line.strip() for line in sys.stdin if line.strip()]
        else:
            base, head, paths = resolve_pr_changed_files(args.head)
            report.update(base=base, head=head)
    except (OSError, ValueError, subprocess.CalledProcessError) as err:
        detail = getattr(err, "stderr", "") or ""
        reason = f"changed files unknown: {err} {detail}".strip()
        selected = ALL_SCOPE_TARGETS
        report["fallback"] = reason
    else:
        if not paths:
            selected = ALL_SCOPE_TARGETS
            report["fallback"] = "no changed files"
        else:
            selected, unknown = classify_scope_paths(paths)
            report.update(files=len(paths), unknown=unknown)

    report["targets"] = sorted(selected)
    if "fallback" in report:
        print(f"::warning::Security runs all audits: {report['fallback']}")
    print(json.dumps(report, sort_keys=True))

    output_path = os.environ.get("GITHUB_OUTPUT", "").strip()
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as stream:
            for key in SECURITY_TARGETS:
                stream.write(f"{key}={'true' if key in selected else 'false'}\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)

    for audit_mode in ("pip", "npm"):
        sub = subparsers.add_parser(audit_mode)
        sub.add_argument("--target", required=True)
        sub.add_argument("--exceptions", required=True)
        sub.add_argument("--today", help="override UTC date (YYYY-MM-DD) for deterministic checks")
        sub.add_argument("--scanner-json", help="read scanner stdout JSON from a file")
        sub.add_argument(
            "--scanner-exit-code",
            type=int,
            default=0,
            help="simulated scanner exit code when --scanner-json is set",
        )

    scope = subparsers.add_parser("scope")
    scope.add_argument("--head", default="HEAD", help="PR merge commit revision (default HEAD)")
    scope.add_argument(
        "--paths-stdin",
        action="store_true",
        help="read newline-separated changed paths from stdin",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.mode == "scope":
        return run_scope(args)
    return run_audit(args)


if __name__ == "__main__":
    sys.exit(main())
