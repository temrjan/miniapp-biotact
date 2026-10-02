"""Bounded transport and scoped authentication, before JSON or database work."""

import hashlib
import hmac
import re
import time

from starlette.requests import Request

from src.employee_referrals.schemas import K1Error

MAX_BODY_BYTES = 1_048_576
HEADER_WINDOW_SECONDS = 30


def decode_key(value: str) -> bytes:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise K1Error("k1_unavailable")
    return bytes.fromhex(value)


def auth_headers(request: Request) -> tuple[str, str]:
    timestamps = request.headers.getlist("x-biotact-k1-timestamp")
    signatures = request.headers.getlist("x-biotact-k1-signature")
    if len(timestamps) != 1 or len(signatures) != 1:
        raise K1Error("k1_invalid_auth")
    stamp, signature = timestamps[0], signatures[0]
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,11})", stamp) or not re.fullmatch(
        r"[0-9a-f]{64}", signature
    ):
        raise K1Error("k1_invalid_auth")
    if abs(int(time.time()) - int(stamp)) > HEADER_WINDOW_SECONDS:
        raise K1Error("k1_invalid_auth")
    return stamp, signature


async def bounded_body(request: Request) -> bytes:
    media = request.headers.getlist("content-type")
    encoding = request.headers.getlist("content-encoding")
    if (
        len(media) != 1
        or not re.fullmatch(
            r'application/json(?:\s*;\s*charset=(?:utf-8|"utf-8"))?\s*', media[0], re.IGNORECASE
        )
        or (encoding and (len(encoding) != 1 or encoding[0].lower() != "identity"))
    ):
        raise K1Error("k1_unsupported_media")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise K1Error("k1_payload_too_large")
        body.extend(chunk)
    return bytes(body)


def verify_signature(key: bytes, path: bytes, stamp: str, signature: str, body: bytes) -> None:
    prefix = b"biotact-k1-v1\nportal-to-shop\nPOST\n" + path + b"\n" + stamp.encode() + b"\n"
    expected = hmac.new(key, prefix + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise K1Error("k1_invalid_auth")
