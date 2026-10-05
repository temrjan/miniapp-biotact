"""Exact frozen K2 transport and a single campaign Telegram attempt."""

import asyncio
import hashlib
import hmac
import re
import time
from dataclasses import dataclass, field
from typing import Annotated, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import Field

from src.employee_referrals.delivery_privacy import private_operation
from src.employee_referrals.notices import Notice, render
from src.employee_referrals.return_schemas import CountSnapshot, load_json
from src.employee_referrals.schemas import PositiveId, StrictModel, UuidText, WireTime

K2_PATH = "/api/v1/internal/employee-referrals/k2"
LIMIT = 16384


@dataclass(frozen=True)
class LaneConfig:
    switch: str = "false"
    origin: str = ""
    key: str = field(default="", repr=False)
    token: str = field(default="", repr=False)
    chat: str = field(default="", repr=False)

    @property
    def enabled(self) -> bool:
        return self.switch == "true"

    def valid(self, lane: str) -> bool:
        if self.switch not in {"true", "false"}:
            return False
        if not self.enabled:
            return True
        if lane == "notice":
            return bool(
                re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", self.token)
                and re.fullmatch(r"-?[1-9][0-9]*", self.chat)
            )
        try:
            url = urlsplit(self.origin)
            return bool(
                url.scheme == "https"
                and url.hostname
                and url.username is None
                and url.password is None
                and not url.path
                and not url.query
                and not url.fragment
                and (url.port is None or 1 <= url.port <= 65535)
                and self.origin.isascii()
                and not any(ord(c) <= 32 or ord(c) == 127 for c in self.origin)
                and not any(c in self.origin for c in ("?", "#", "\\"))
                and re.fullmatch(r"[0-9a-f]{64}", self.key)
            )
        except ValueError:
            return False


class Receipt(StrictModel):
    event_id: UuidText
    source: Literal["biotact-shop"]
    order_id: PositiveId
    order_version: PositiveId
    source_position: PositiveId
    body_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    outcome: Literal["applied", "superseded"]
    received_at: WireTime


class Projection(StrictModel):
    order_version: PositiveId
    accounting_state: Literal["confirmed", "incomplete", "inconsistent"]
    has_conflict: bool


class Ack(StrictModel):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    receipt: Receipt
    replayed: bool
    projection: Projection


def parse_ack(raw: bytes, snapshot: CountSnapshot, digest: str) -> Ack:
    ack = Ack.model_validate(load_json(raw))
    r = ack.receipt
    if (
        (r.event_id, r.source, r.order_id, r.order_version, r.source_position, r.body_sha256)
        != (
            snapshot.event_id,
            snapshot.source,
            snapshot.order_id,
            snapshot.order_version,
            snapshot.source_position,
            digest,
        )
        or ack.projection.order_version < r.order_version
        or (r.outcome == "superseded" and ack.projection.order_version <= r.order_version)
    ):
        raise ValueError("Invalid receipt")
    return ack


@dataclass(frozen=True, repr=False)
class Result:
    ack: Ack | None = None
    message_id: int | None = None
    category: str = "protocol"
    permanent: bool = False
    retry_after: int = 0


def headers(key: str, body: bytes, timestamp: int) -> dict[str, str]:
    if type(timestamp) is not int or not 0 <= timestamp <= 999999999999:
        raise ValueError("Invalid transport time")
    prefix = f"biotact-k2-v1\nshop-to-portal\nPOST\n{K2_PATH}\n{timestamp}\n".encode()
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "X-Biotact-K2-Timestamp": str(timestamp),
        "X-Biotact-K2-Signature": hmac.new(
            bytes.fromhex(key), prefix + body, hashlib.sha256
        ).hexdigest(),
    }


async def response_bytes(response: httpx.Response) -> bytes:
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise ValueError("Invalid encoding")
    raw = bytearray()
    async for chunk in response.aiter_raw():
        if len(raw) + len(chunk) > LIMIT:
            raise ValueError("Invalid response size")
        raw.extend(chunk)
    return bytes(raw)


def refusal(status: int, raw: bytes) -> Result:
    try:
        value = load_json(raw)
        code = (
            value.get("detail", {}).get("code")
            if isinstance(value, dict)
            and isinstance(value.get("detail"), dict)
            and set(value) == {"detail"}
            else None
        )
    except (ValueError, RecursionError):
        code = None
    if status == 401 and code == "k2_invalid_auth":
        return Result(category="authentication", permanent=True)
    if status == 409 and code in {
        "k2_event_conflict",
        "k2_version_conflict",
        "k2_identity_conflict",
        "k2_unknown_attribution",
    }:
        return Result(category="conflict", permanent=True)
    if status == 413:
        return Result(category="payload_limit", permanent=True)
    if status == 415:
        return Result(category="protocol", permanent=True)
    if status == 422 and code == "k2_invalid_request":
        return Result(category="invalid_request", permanent=True)
    if status == 404 and code == "k2_disabled":
        return Result(category="receiver_disabled")
    return Result(category="transport" if status == 429 or 500 <= status <= 599 else "protocol")


@private_operation
async def send_k2(
    client: httpx.AsyncClient, config: LaneConfig, body: bytes, snapshot: CountSnapshot, digest: str
) -> Result:
    if not config.enabled or not config.valid("k2"):
        return Result(category="configuration", permanent=True)
    try:
        async with asyncio.timeout(10):
            async with client.stream(
                "POST",
                config.origin + K2_PATH,
                content=body,
                headers=headers(config.key, body, int(time.time())),
                timeout=10,
                follow_redirects=False,
            ) as response:
                raw = await response_bytes(response)
                if response.status_code != 200:
                    return refusal(response.status_code, raw)
                if (
                    response.headers.get("content-type", "").split(";", 1)[0].lower()
                    != "application/json"
                    or response.headers.get("cache-control", "").lower() != "no-store"
                ):
                    return Result()
                return Result(ack=parse_ack(raw, snapshot, digest))
    except (httpx.HTTPError, OSError, TimeoutError):
        return Result(category="transport")
    except (ValueError, RecursionError):
        return Result()


@private_operation
async def send_notice(client: httpx.AsyncClient, config: LaneConfig, notice: Notice) -> Result:
    if not config.enabled or not config.valid("notice"):
        return Result(category="configuration", permanent=True)
    try:
        message = render(notice)
    except (ValueError, ArithmeticError):
        return Result(category="storage", permanent=True)
    try:
        async with asyncio.timeout(10):
            async with client.stream(
                "POST",
                f"https://api.telegram.org/bot{config.token}/sendMessage",
                json={"chat_id": config.chat, "text": message, "parse_mode": "HTML"},
                headers={"Accept-Encoding": "identity"},
                timeout=10,
                follow_redirects=False,
            ) as response:
                raw = await response_bytes(response)
                if response.status_code in {401, 403}:
                    return Result(category="configuration", permanent=True)
                if response.status_code == 400:
                    return Result(category="recipient_unreachable", permanent=True)
                if 500 <= response.status_code <= 599:
                    return Result(category="transport")
                try:
                    value = load_json(raw)
                except (ValueError, RecursionError):
                    if response.status_code != 429:
                        return Result()
                    value = None
                if response.status_code == 429:
                    retry = (
                        value.get("parameters", {}).get("retry_after", 0)
                        if isinstance(value, dict) and isinstance(value.get("parameters"), dict)
                        else 0
                    )
                    return Result(
                        category="transport",
                        retry_after=min(retry, 7200) if type(retry) is int and retry > 0 else 0,
                    )
                if (
                    response.status_code != 200
                    or not isinstance(value, dict)
                    or value.get("ok") is not True
                    or not isinstance(value.get("result"), dict)
                ):
                    return Result()
                identity = value["result"].get("message_id")
                if type(identity) is not int or not 1 <= identity <= 9223372036854775807:
                    return Result()
                return Result(message_id=identity)
    except (httpx.HTTPError, OSError, TimeoutError):
        return Result(category="transport")
    except (ValueError, RecursionError):
        return Result()
