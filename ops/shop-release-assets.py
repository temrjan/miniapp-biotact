"""Keep verified generated Shop assets paired with their source revision."""

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, cast

OWNERS = ("packages/frontend", "admin-panel-v2")
KIND = "shop-release-assets-v1"


def now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def inventory(root: Path) -> list[dict[str, Any]]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"Unsafe or missing asset directory: {root}")
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Asset symlink refused: {path}")
        if path.is_file():
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "mode": path.stat().st_mode & 0o777,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        elif not path.is_dir():
            raise ValueError(f"Special asset file refused: {path}")
    if not any(item["path"] == "index.html" for item in files):
        raise ValueError(f"Asset index missing: {root}")
    return files


def write_metadata(snapshot: Path, metadata: dict[str, Any]) -> None:
    temporary = snapshot / "manifest.next.json"
    temporary.write_text(json.dumps(metadata, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, snapshot / "manifest.json")


def load_snapshot(store: Path, snapshot: Path) -> dict[str, Any]:
    if (
        snapshot.is_symlink()
        or snapshot.parent != store
        or not re.fullmatch(r"snapshot-[a-z0-9_]+", snapshot.name)
    ):
        raise ValueError("Snapshot outside the owned store")
    path = snapshot / "manifest.json"
    if path.is_symlink():
        raise ValueError("Snapshot metadata symlink refused")
    metadata = cast(dict[str, Any], json.loads(path.read_text()))
    if not isinstance(metadata, dict):
        raise ValueError("Unknown snapshot metadata shape")
    if metadata.get("kind") != KIND or metadata.get("project") != str(store.parent.parent):
        raise ValueError("Unknown snapshot owner")
    if not isinstance(metadata.get("commit"), str) or not re.fullmatch(
        r"[0-9a-f]{40}", metadata["commit"]
    ):
        raise ValueError("Invalid snapshot source revision")
    if not isinstance(metadata.get("artifacts"), dict) or set(metadata["artifacts"]) != set(OWNERS):
        raise ValueError("Incomplete snapshot")
    if "successful_at" in metadata and not isinstance(metadata["successful_at"], str):
        raise ValueError("Invalid success marker")
    return metadata


def verify(snapshot: Path, metadata: dict[str, Any]) -> None:
    for owner in OWNERS:
        if inventory(snapshot / "assets" / owner) != metadata["artifacts"][owner]:
            raise ValueError(f"Snapshot integrity failure: {owner}")


def capture(project: Path, store: Path, commit: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid source revision")
    expected = {owner: inventory(project / owner / "dist") for owner in OWNERS}
    snapshot = Path(tempfile.mkdtemp(prefix="snapshot-", dir=store))
    for owner in OWNERS:
        shutil.copytree(project / owner / "dist", snapshot / "assets" / owner)
    metadata = {
        "kind": KIND,
        "project": str(project),
        "commit": commit,
        "created_at": now(),
        "artifacts": expected,
        "recovery_open": False,
    }
    verify(snapshot, metadata)
    if expected != {owner: inventory(project / owner / "dist") for owner in OWNERS}:
        raise ValueError("Live assets changed during capture")
    write_metadata(snapshot, metadata)
    return snapshot


def restore(project: Path, snapshot: Path, metadata: dict[str, Any]) -> None:
    verify(snapshot, metadata)
    attempt = Path(tempfile.mkdtemp(prefix="restore-", dir=snapshot))
    # Prepare and verify BOTH trees before changing either live directory.
    for owner in OWNERS:
        shutil.copytree(snapshot / "assets" / owner, attempt / "ready" / owner)
        if inventory(attempt / "ready" / owner) != metadata["artifacts"][owner]:
            raise ValueError("Prepared restoration failed integrity verification")
        live = project / owner / "dist"
        if live.is_symlink() or (live.exists() and not live.is_dir()):
            raise ValueError("Unsafe live asset target")
    metadata["recovery_open"] = True
    write_metadata(snapshot, metadata)
    for owner in OWNERS:
        live = project / owner / "dist"
        if live.exists():
            failed = attempt / "failed" / owner
            failed.parent.mkdir(parents=True, exist_ok=True)
            os.replace(live, failed)
        os.replace(attempt / "ready" / owner, live)
        if inventory(live) != metadata["artifacts"][owner]:
            raise ValueError("Live restoration failed integrity verification")
    metadata["restored_at"] = now()
    write_metadata(snapshot, metadata)


def prune(store: Path, keep: int) -> list[str]:
    if keep != 3:
        raise ValueError("Keep exactly three successful source versions")
    eligible = []
    for snapshot in store.glob("snapshot-*"):
        try:
            metadata = load_snapshot(store, snapshot)
        except (OSError, ValueError, KeyError):
            # Unknown/incomplete contents are preserved, never guessed disposable.
            continue
        if metadata.get("successful_at") and not metadata.get("recovery_open"):
            eligible.append((metadata["successful_at"], metadata["commit"], snapshot))
    eligible.sort(reverse=True)
    retained: set[str] = set()
    removed = []
    for _, commit, snapshot in eligible:
        if commit not in retained and len(retained) < keep:
            retained.add(commit)
            continue
        shutil.rmtree(snapshot)
        removed.append(snapshot.name)
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "restore", "success", "resolved"))
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--commit")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--prune", action="store_true")
    args = parser.parse_args()
    project = args.project.absolute()
    if project.is_symlink() or project != project.resolve():
        raise ValueError("Project path must be canonical")
    store = project / ".venv" / "shop-release-assets"
    for directory in (project / ".venv", store):
        if directory.is_symlink():
            raise ValueError("Snapshot store symlink refused")
        directory.mkdir(exist_ok=True)
    if args.action == "capture":
        print(capture(project, store, args.commit or ""))
        return
    if args.snapshot is None:
        raise ValueError("Snapshot required")
    snapshot = args.snapshot.absolute()
    metadata = load_snapshot(store, snapshot)
    if args.action == "restore":
        restore(project, snapshot, metadata)
    elif args.action == "resolved":
        metadata["recovery_open"] = False
        metadata["resolved_at"] = now()
        write_metadata(snapshot, metadata)
    else:
        verify(snapshot, metadata)
        metadata["successful_at"] = now()
        write_metadata(snapshot, metadata)
    removed = prune(store, 3) if args.action == "success" and args.prune else []
    print(
        json.dumps(
            {
                "action": args.action,
                "commit": metadata["commit"],
                "snapshot": str(snapshot),
                "removed": removed,
            }
        )
    )


if __name__ == "__main__":
    main()
