"""Bounded password work and shared input rules for named admin accounts."""

import asyncio
import re
from collections.abc import Iterator
from contextlib import contextmanager

import anyio
import bcrypt

LOGIN_PATTERN = r"[a-z][a-z0-9_.-]{2,63}"
HASH_PATTERN = r"\$2b\$12\$[./A-Za-z0-9]{53}"
COMPAT_LOGIN = "biotact"
# Public synthetic comparison hash; never generated for each unknown login.
DUMMY_HASH = "$2b$12$3Ql9LCk70bIhxeig0t5qtuQ1E7wzSCgiy939WCFGU/zNOmSw4qENq"


class PasswordServiceUnavailable(Exception):
    """Value-free service refusal, including occupied verification capacity."""


def normalize_login(value: str, *, allow_compat: bool = False) -> str:
    login = value.strip(" \t\r\n\v\f")
    if not login.isascii():
        raise ValueError("Invalid login")
    login = login.lower()
    if not re.fullmatch(LOGIN_PATTERN, login) or (login == COMPAT_LOGIN and not allow_compat):
        raise ValueError("Invalid login")
    return login


def password_bytes(value: str, *, creating: bool = False) -> bytes:
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("Invalid password") from None
    if not encoded or len(encoded) > 72 or (creating and len(value) < 8):
        raise ValueError("Invalid password")
    return encoded


def make_hash(value: str) -> str:
    return bcrypt.hashpw(password_bytes(value, creating=True), bcrypt.gensalt(rounds=12)).decode(
        "ascii"
    )


async def check_hash(value: bytes, stored: str) -> bool:
    if not re.fullmatch(HASH_PATTERN, stored):
        raise PasswordServiceUnavailable()
    worker = asyncio.create_task(
        anyio.to_thread.run_sync(bcrypt.checkpw, value, stored.encode("ascii"))
    )
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # asyncio cancellation can bypass AnyIO's cancellation scope. Keep the
        # admission slot until the thread finishes, including repeated cancels.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()  # consume a worker error without printing its payload
        raise
    except ValueError:
        raise PasswordServiceUnavailable() from None


class VerificationCapacity:
    """Per-process admission on the API event loop; overflow has no waiting queue."""

    def __init__(self) -> None:
        self.in_flight = 0

    @contextmanager
    def admit(self) -> Iterator[None]:
        if self.in_flight >= 4:
            raise PasswordServiceUnavailable()
        self.in_flight += 1
        try:
            yield
        finally:
            self.in_flight -= 1
