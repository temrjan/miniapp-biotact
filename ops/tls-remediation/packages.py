#!/usr/bin/env python3
"""Bounded offline repair and original-path recovery. Callers hold admission closed."""

# Commands use fixed executables/arguments and explicitly selected owned fixture/artifact paths.
# ruff: noqa: S603

import argparse
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import tarfile
import uuid
from pathlib import Path
from typing import Any, cast

ARTIFACTS = {
    "pip": (
        "pip-26.2.2-py3-none-any.whl",
        "fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70",
    ),
    "certifi": (
        "certifi-2026.10.19-py3-none-any.whl",
        "42d35340cc5b9ba78e818ddbff0c5b1c2b779de448a340fd1b989f5fa8670e1b",
    ),
    "uv": ("uv", "8a70db72685b7138121bfcda71fa1a00cb709ed9f1fdc4ecf1dd871e8e167e52"),
    "ca": (
        "ca-certificates_20260816ubuntu0.24.04.1_all.deb",
        "8a0a754aaafed72cc8efb8c1731808133284a84fbfc76a4fcc73fce9e01b4187",
    ),
}


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verified(directory: Path, name: str) -> Path:
    filename, expected = ARTIFACTS[name]
    path = directory / filename
    require(path.is_file() and not path.is_symlink(), "artifact must be a regular file")
    require(digest(path) == expected, "artifact digest mismatch: " + name)
    return path


def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, text=True, timeout=120, **kwargs)


def environment(path: Path) -> dict[str, Any]:
    require(path.is_absolute() and path.resolve() == path, "use resolved original venv path")
    script = """import importlib.metadata as m,json,sys,os
rows={}
for d in m.distributions():
 n=d.metadata['Name'].lower().replace('_','-').replace('.','-')
 if n in rows:raise ValueError('duplicate distribution '+n)
 rows[n]=d.version
print(json.dumps(dict(prefix=sys.prefix,base_prefix=sys.base_prefix,executable=sys.executable,
 real_executable=os.path.realpath(sys.executable),version=list(sys.version_info[:3]),packages=rows)))"""
    result = json.loads(
        run([str(path / "bin/python"), "-I", "-B", "-c", script], capture_output=True).stdout
    )
    require(Path(result["prefix"]).resolve() == path, "interpreter belongs to another environment")
    require(isinstance(result, dict), "invalid environment map")
    return cast(dict[str, Any], result)


def compare(before: dict[str, Any], after: dict[str, Any], packages: list[str]) -> None:
    for key in ("prefix", "base_prefix", "real_executable", "version"):
        require(before[key] == after[key], "interpreter identity changed: " + key)
    wanted = {"pip": "26.2.2", "certifi": "2026.10.19"}
    expected = dict(before["packages"])
    for name in packages:
        require(name in expected, "refuse adding a previously absent distribution: " + name)
        expected[name] = wanted[name]
    require(after["packages"] == expected, "unexpected complete distribution delta")


def stopped(path: Path) -> None:
    """Inspect executable arguments, cwd and descriptors; never print command secrets."""
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            args = (proc / "cmdline").read_bytes().split(b"\0")
            cwd = (proc / "cwd").resolve()
            executable = Path(os.fsdecode(args[0]))
            candidates = [str(executable), str(cwd)]
            if args[0]:
                if not executable.is_absolute():
                    executable = cwd / executable
                # Resolve current/release directory aliases, but retain bin/python:
                # resolving the final interpreter link loses its venv association.
                candidates.append(str(executable.parent.resolve() / executable.name))
            candidates += [str(fd.resolve()) for fd in (proc / "fd").iterdir()]
            require(
                not any(s == str(path) or s.startswith(str(path) + "/") for s in candidates),
                "environment still in use by PID " + proc.name,
            )
        except (FileNotFoundError, ProcessLookupError):
            require(not proc.exists(), "cannot attest all environment consumers")
            continue
        except PermissionError as exc:
            raise ValueError("cannot attest all environment consumers") from exc


def tree(path: Path) -> dict[str, Any]:
    result = {}
    for base, dirs, files in os.walk(path, followlinks=False):
        for p in [Path(base), *(Path(base) / n for n in dirs + files)]:
            rel = str(p.relative_to(path))
            st = p.lstat()
            row: dict[str, Any] = {
                "mode": stat.S_IMODE(st.st_mode),
                "uid": st.st_uid,
                "gid": st.st_gid,
            }
            if p.is_symlink():
                row["link"] = os.readlink(p)
            elif p.is_file():
                row["sha256"] = digest(p)
            else:
                require(p.is_dir(), "unsupported special environment member")
            result[rel] = row
    return result


def archive(path: Path, output: Path) -> None:
    stopped(path)
    require(
        not output.exists() and not output.with_suffix(".json").exists(), "archive already exists"
    )
    before = environment(path)
    manifest = tree(path)
    run(
        [
            "tar",
            "--acls",
            "--xattrs",
            "--numeric-owner",
            "-cpf",
            str(output),
            "-C",
            str(path.parent),
            path.name,
        ]
    )
    os.chmod(output, 0o600)
    run(["tar", "--acls", "--xattrs", "--compare", "-f", str(output), "-C", str(path.parent)])
    require(tree(path) == manifest, "environment changed during archive")
    output.with_suffix(".json").write_text(
        json.dumps(
            {
                "original": str(path),
                "archive_sha256": digest(output),
                "environment": before,
                "tree": manifest,
            },
            indent=2,
        )
        + "\n"
    )
    os.chmod(output.with_suffix(".json"), 0o600)


def restore(path: Path, source: Path) -> None:
    stopped(path)
    record = json.loads(source.with_suffix(".json").read_text())
    require(record["original"] == str(path), "restore must use the recorded original path")
    require(digest(source) == record["archive_sha256"], "archive corrupt")
    with tarfile.open(source) as tar:
        for item in tar:
            rel = Path(item.name)
            require(
                not rel.is_absolute() and ".." not in rel.parts and rel.parts[0] == path.name,
                "unsafe archive member",
            )
            require(
                item.isfile() or item.isdir() or item.issym() or item.islnk(),
                "unsafe archive member type",
            )
    damaged = path.with_name(path.name + ".tls-damaged-" + uuid.uuid4().hex)
    path.rename(damaged)
    run(
        [
            "tar",
            "--acls",
            "--xattrs",
            "--same-permissions",
            "--numeric-owner",
            "-xpf",
            str(source),
            "-C",
            str(path.parent),
        ]
    )
    require(tree(path) == record["tree"], "restored environment metadata/content mismatch")
    require(environment(path) == record["environment"], "restored interpreter/package mismatch")
    print(
        json.dumps(
            {
                "damaged": str(damaged),
                "restart_allowed": False,
                "required": "offline forward repair before any restart",
            }
        )
    )


def repair(path: Path, directory: Path, name: str) -> None:
    stopped(path)
    before = environment(path)
    require(name in before["packages"], "refuse installing an absent package (Team stays pipless)")
    uv, wheel = verified(directory, "uv"), verified(directory, name)
    owner = path.stat().st_uid
    require(os.geteuid() in (0, owner), "installer is not the environment owner")
    prefix = (
        []
        if owner == os.geteuid()
        else ["/usr/sbin/runuser", "-u", pwd.getpwuid(owner).pw_name, "--"]
    )
    run(
        [
            *prefix,
            str(uv),
            "--no-config",
            "--no-cache",
            "--offline",
            "pip",
            "install",
            "--python",
            str(path / "bin/python"),
            "--no-index",
            "--no-deps",
            "--reinstall",
            str(wheel),
        ]
    )
    compare(before, environment(path), [name])
    run(
        [
            *prefix,
            str(uv),
            "--no-config",
            "--no-cache",
            "--offline",
            "pip",
            "check",
            "--python",
            str(path / "bin/python"),
        ]
    )


def apt_simulation(text: str) -> None:
    changes = re.findall(r"^(Inst|Remv|Conf) (\S+)(.*)$", text, re.M)
    require(bool(changes), "APT simulation contained no actions")
    require(
        all(op != "Remv" and name == "ca-certificates" for op, name, _ in changes),
        "APT selected unrelated changes",
    )
    require(
        any(op == "Inst" and "20260816ubuntu0.24.04.1" in detail for op, _, detail in changes),
        "APT did not select the fixed CA package",
    )
    require("0 to remove" in text, "APT removal summary missing")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("map", "archive", "restore", "repair"):
        p = sub.add_parser(name)
        p.add_argument("path", type=Path)
        if name in ("archive", "restore"):
            p.add_argument("archive", type=Path)
        if name == "repair":
            p.add_argument("artifacts", type=Path)
            p.add_argument("package", choices=("pip", "certifi"))
    p = sub.add_parser("verify")
    p.add_argument("artifacts", type=Path)
    p = sub.add_parser("apt-simulation")
    p.add_argument("log", type=Path)
    args = parser.parse_args()
    if args.command == "map":
        print(json.dumps(environment(args.path), indent=2))
    elif args.command == "verify":
        print(json.dumps({n: str(verified(args.artifacts, n)) for n in ARTIFACTS}, indent=2))
    elif args.command == "apt-simulation":
        apt_simulation(args.log.read_text())
    elif args.command == "archive":
        archive(args.path, args.archive)
    elif args.command == "restore":
        restore(args.path, args.archive)
    else:
        repair(args.path, args.artifacts, args.package)


if __name__ == "__main__":
    main()
