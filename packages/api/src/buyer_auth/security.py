"""Purpose-separated browser proofs and explicit application email equality."""

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta

from src.config import Settings

CONTEXT_COOKIE = "__Host-biotact_auth"
SESSION_COOKIE = "__Host-biotact_buyer"
SECRET_PATTERN = r"[0-9a-f]{64}"
HANDLE_PATTERN = r"[0-9a-f]{32}"
CONTEXT_LIFETIME = timedelta(minutes=30)
PROOF_LIFETIME = timedelta(minutes=10)
IDLE_LIFETIME = timedelta(days=30)
ABSOLUTE_LIFETIME = timedelta(days=90)


class BuyerAuthError(Exception):
    """Value-free HTTP error; never attach submitted values or underlying exceptions."""

    def __init__(self, status: int = 401, code: str = "invalid_authentication", retry: int = 0):
        self.status = status
        self.code = code
        self.retry = retry
        super().__init__(code)


def now() -> datetime:
    return datetime.now(UTC)


def normalize_email(value: str) -> str:
    if not value.isascii() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Invalid email")
    value = value.strip(" ").lower()
    if len(value) > 254 or value.count("@") != 1:
        raise ValueError("Invalid email")
    local, domain = value.split("@")
    atom = r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+"
    if len(local) > 64 or not re.fullmatch(rf"{atom}(?:\.{atom})*", local):
        raise ValueError("Invalid email")
    labels = domain.split(".")
    if len(labels) < 2 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
    ):
        raise ValueError("Invalid email")
    return value


def keyed(config: Settings, purpose: str, *parts: str) -> str:
    key = config.buyer_protection_key.get_secret_value().encode("utf-8")
    if len(key) < 32:
        raise BuyerAuthError(503, "authentication_unavailable")
    # Length-prefixed components remove delimiter ambiguity across arbitrary inputs.
    payload = b"".join(len(p.encode()).to_bytes(4, "big") + p.encode() for p in (purpose, *parts))
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def verifier_digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def new_context(config: Settings, at: datetime) -> str:
    body = f"{int(at.timestamp())}.{secrets.token_hex(32)}"
    return body + "." + keyed(config, "context-cookie", body)


def context_deadline(config: Settings, value: str | None, at: datetime) -> datetime:
    if not value or len(value) > 150:
        raise BuyerAuthError()
    parts = value.split(".")
    if len(parts) != 3 or not re.fullmatch(r"[0-9]{1,12}", parts[0]):
        raise BuyerAuthError()
    if not re.fullmatch(SECRET_PATTERN, parts[1]) or not re.fullmatch(SECRET_PATTERN, parts[2]):
        raise BuyerAuthError()
    if not hmac.compare_digest(parts[2], keyed(config, "context-cookie", ".".join(parts[:2]))):
        raise BuyerAuthError()
    try:
        created = datetime.fromtimestamp(int(parts[0]), UTC)
    except (ValueError, OverflowError, OSError):
        raise BuyerAuthError() from None
    deadline = created + CONTEXT_LIFETIME
    if created > at or at >= deadline:
        raise BuyerAuthError()
    return deadline


def csrf(config: Settings, value: str, *, generation: int | None = None) -> str:
    return keyed(
        config,
        "context-csrf" if generation is None else "session-csrf",
        value,
        "" if generation is None else str(generation),
    )


def require_csrf(expected: str, supplied: str | None) -> None:
    if not supplied or not supplied.isascii() or not hmac.compare_digest(expected, supplied):
        raise BuyerAuthError(403, "request_forbidden")
