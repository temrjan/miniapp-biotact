"""Load the single trusted, operator-installed admission implementation."""

import importlib.util
import stat
import sys
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Protocol, cast

ADMISSION_VERSION = 1
LIBRARY = Path("/usr/local/lib/biotact-referral-maintenance/referral_admission.py")


class Provider(Protocol):
    def admission(self, role: str) -> AbstractAsyncContextManager[bool]: ...
    def register_participant(self, role: str) -> None: ...
    def observation(self) -> tuple[str, str]: ...


_loaded: Provider | None = None


def provider() -> Provider | None:
    global _loaded
    if _loaded is not None:
        return _loaded
    try:
        parent = LIBRARY.parent.lstat()
    except FileNotFoundError:
        parent = None
    if parent is not None and (
        not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022
    ):
        raise ValueError("Untrusted admission directory")
    try:
        info = LIBRARY.lstat()
    except FileNotFoundError:
        try:
            Path("/var/lib/biotact-referral-maintenance/state.json").lstat()
        except FileNotFoundError:
            return None
        raise ValueError("Missing maintenance library") from None
    if parent is None or not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError("Untrusted admission library")
    spec = importlib.util.spec_from_file_location("_biotact_referral_admission", LIBRARY)
    if spec is None or spec.loader is None:
        raise ValueError("Missing admission loader")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    version = getattr(module, "ADMISSION_VERSION", None)
    if (
        type(version) is not int
        or version != ADMISSION_VERSION
        or any(
            not callable(getattr(module, name, None))
            for name in ("admission", "register_participant", "observation")
        )
    ):
        raise ValueError("Incompatible admission library")
    _loaded = cast(Provider, module)  # Versioned, trusted runtime module boundary.
    return _loaded


@asynccontextmanager
async def admission(role: str) -> AsyncIterator[bool]:
    try:
        implementation = provider()
    except (OSError, ValueError, ImportError, SyntaxError):
        yield False
        return
    if implementation is None:
        yield True  # Legacy bootstrap only; guard preflight rejects no registration.
        return
    async with implementation.admission(role) as allowed:
        yield allowed


def register_participant(role: str) -> None:
    implementation = provider()
    if implementation is not None:
        implementation.register_participant(role)


def observation() -> tuple[str, str]:
    try:
        implementation = provider()
        return ("none", "") if implementation is None else implementation.observation()
    except (OSError, ValueError, ImportError, SyntaxError):
        return "failed", ""
