"""Select the CI jobs whose inputs a change touches.

The CI workflow runs this script in its first job and starts every other job only
when the script reports ``<job>=true``. A path that no rule classifies runs every
job, and so does any failure to establish the changed files.

Modes:
  pull-request  Files that the tested merge commit changes against its base parent.
  push          Files changed since the newest commit on ``main`` whose CI push run
                succeeded, so a failed or cancelled run is never skipped over.
  paths         Newline-separated paths on standard input (dry runs).
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable

JOBS = (
    "release_recovery",
    "frontend",
    "admin",
    "api",
    "migration_parity",
    "bot",
    "telegram_bot_api",
)
ALL = frozenset(JOBS)
NO_JOBS: frozenset[str] = frozenset()
DEPLOY_PAYLOAD_READERS = frozenset({"release_recovery", "api"})
API_READERS = frozenset({"api", "migration_parity", "telegram_bot_api"})
BOT_READERS = frozenset({"bot", "telegram_bot_api"})

# The first matching rule wins. An entry ending in "/" matches a directory tree;
# any other entry matches one file. A path without a matching rule runs every job.
RULES: tuple[tuple[str, frozenset[str]], ...] = (
    (".github/workflows/ci.yml", ALL),
    (".github/scripts/ci-changes.py", ALL),
    # API tests and the release recovery test execute the deploy payload.
    (".github/workflows/deploy.yml", DEPLOY_PAYLOAD_READERS),
    # API tests and lint read ops/ scripts; the release recovery job tests them.
    ("ops/", DEPLOY_PAYLOAD_READERS),
    ("packages/api/", API_READERS),
    ("packages/bot/", BOT_READERS),
    ("packages/frontend/", frozenset({"frontend"})),
    ("admin-panel-v2/", frozenset({"admin"})),
    # Documentation only. Keep equal to the push paths-ignore list in ci.yml.
    ("docs/", NO_JOBS),
    (".claude/", NO_JOBS),
    (".kimi/", NO_JOBS),
)
SHA = re.compile(r"[0-9a-f]{40}")
RUNS_QUERY = (
    "repos/{repo}/actions/workflows/ci.yml/runs?branch=main&event=push&status=success&per_page=100"
)


def jobs_for(path: str) -> frozenset[str] | None:
    """Return the jobs that read ``path``, or ``None`` when no rule covers it."""
    for rule, jobs in RULES:
        if path == rule or (rule.endswith("/") and path.startswith(rule)):
            return jobs
    if "/" not in path and path.endswith(".md"):
        return NO_JOBS  # root-level Markdown, the push filter '*.md'
    return None


def classify(paths: Iterable[str]) -> tuple[frozenset[str], list[str]]:
    """Return the selected jobs and the paths that no rule covers."""
    selected: set[str] = set()
    unknown = []
    for path in paths:
        jobs = jobs_for(path)
        if jobs is None:
            unknown.append(path)
        else:
            selected |= jobs
    return (ALL if unknown else frozenset(selected)), unknown


def git(*args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed git argument vector
        ["git", *args],  # noqa: S607 - git from the runner PATH
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def commit(revision: str) -> str:
    return git("rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}").strip()


def changed_files(base: str, head: str) -> list[str]:
    output = git("diff", "--name-only", "--no-renames", "-z", base, head, "--")
    return [path for path in output.split("\0") if path]


def pull_request_base(head: str) -> str:
    parents = git("rev-list", "--parents", "-n", "1", head).split()[1:]
    if len(parents) != 2:
        raise ValueError(f"{head} is not a two-parent merge commit")
    return parents[0]


def last_successful_push(repo: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed gh argument vector
        ["gh", "api", RUNS_QUERY.format(repo=repo)],  # noqa: S607 - gh from the runner PATH
        check=True,
        capture_output=True,
        text=True,
    )
    runs = json.loads(result.stdout)["workflow_runs"]
    if not runs:
        raise ValueError("no successful CI push run on main")
    base = str(max(runs, key=lambda run: int(run["run_number"]))["head_sha"])
    if not SHA.fullmatch(base):
        raise ValueError(f"unexpected head_sha {base!r}")
    return base


def detect(args: argparse.Namespace) -> tuple[frozenset[str], dict[str, object]]:
    """Return the selected jobs and a report for the log."""
    report: dict[str, object] = {"mode": args.mode}
    try:
        if args.mode == "paths":
            paths = [line.strip() for line in sys.stdin if line.strip()]
        else:
            head = commit(args.head)
            if args.mode == "pull-request":
                base = pull_request_base(head)
            else:
                base = commit(last_successful_push(args.repo))
            report.update(base=base, head=head)
            paths = changed_files(base, head)
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        detail = getattr(error, "stderr", "") or ""
        report.update(fallback=f"changed files unknown: {error} {detail}".strip())
        return ALL, report
    jobs, unknown = classify(paths)
    if not paths:
        jobs = ALL
        report["fallback"] = "no changed files"
    report.update(files=len(paths), unknown=unknown)
    return jobs, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Select the CI jobs whose inputs changed.")
    parser.add_argument("mode", choices=("pull-request", "push", "paths"))
    parser.add_argument("--head", default="HEAD", help="commit under test (default HEAD)")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    args = parser.parse_args()
    if args.mode == "push" and not args.repo:
        parser.error("push needs --repo or GITHUB_REPOSITORY")
    selected, report = detect(args)
    report["jobs"] = sorted(selected)
    print(json.dumps(report, sort_keys=True))
    if "fallback" in report:
        print(f"::warning::CI runs every job: {report['fallback']}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            for job in JOBS:
                stream.write(f"{job}={'true' if job in selected else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
