"""Strict K1 wire values and canonical content; no transport or storage effects."""

import base64
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    model_validator,
)

MAX_BIGINT = 9223372036854775807
LEASE_SECONDS = 300
State = Literal["published", "ended"]
Outcome = Literal["applied", "renewed", "unchanged", "superseded"]
ErrorCode = Literal[
    "k1_disabled",
    "k1_invalid_auth",
    "k1_payload_too_large",
    "k1_unsupported_media",
    "k1_invalid_request",
    "k1_event_conflict",
    "k1_revision_conflict",
    "k1_identity_conflict",
    "k1_campaign_conflict",
    "k1_unavailable",
]


class K1Error(Exception):
    """Only a fixed public code may cross the K1 boundary."""

    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(code)


def utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def wire_time(value: object) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z", value
    ):
        raise ValueError("Invalid UTC timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def canonical_uuid(value: str) -> str:
    parsed = UUID(value)
    if parsed.version != 4 or parsed.variant != "specified in RFC 4122" or str(parsed) != value:
        raise ValueError("Invalid canonical UUIDv4")
    return value


def canonical_token(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{22}", value):
        raise ValueError("Invalid token")
    decoded = base64.urlsafe_b64decode(value + "==")
    if base64.urlsafe_b64encode(decoded).decode().rstrip("=") != value:
        raise ValueError("Invalid canonical token")
    return value


def scalar_name(value: str) -> str:
    if not value.strip() or any(ord(c) == 0 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError("Invalid name")
    return value


PositiveId = Annotated[int, Field(strict=True, ge=1, le=MAX_BIGINT)]
UuidText = Annotated[str, AfterValidator(canonical_uuid)]
Token = Annotated[str, AfterValidator(canonical_token)]
WireTime = Annotated[datetime, BeforeValidator(wire_time), PlainSerializer(utc_text)]
AckTime = Annotated[datetime, PlainSerializer(utc_text)]


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class Campaign(StrictModel):
    id: PositiveId
    content_revision: PositiveId
    name: Annotated[str, Field(max_length=200), AfterValidator(scalar_name)]
    product_id: PositiveId
    state: State
    published_at: WireTime
    start_at: WireTime
    end_at: WireTime
    ended_at: WireTime | None

    @model_validator(mode="after")
    def dates(self) -> Self:
        if self.end_at <= self.start_at:
            raise ValueError("Invalid campaign window")
        if self.state == "published" and self.ended_at is not None:
            raise ValueError("Published has no end transition")
        if self.state == "ended" and (self.ended_at is None or self.ended_at < self.published_at):
            raise ValueError("Invalid end transition")
        return self


class Participant(StrictModel):
    employee_code: UuidText
    token: Token
    employee_display_name: Annotated[str, Field(max_length=161), AfterValidator(scalar_name)]


class Snapshot(StrictModel):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    event_id: UuidText
    campaign: Campaign
    participants: list[Participant]
    issued_at: WireTime

    @model_validator(mode="after")
    def consistency(self) -> Self:
        if self.campaign.published_at > self.issued_at or (
            self.campaign.ended_at is not None and self.campaign.ended_at > self.issued_at
        ):
            raise ValueError("Issuance precedes publication/end")
        if len({p.employee_code for p in self.participants}) != len(self.participants) or len(
            {p.token for p in self.participants}
        ) != len(self.participants):
            raise ValueError("Duplicate identity")
        return self

    def content_sha256(self) -> str:
        content = self.model_dump(mode="json", exclude={"event_id", "issued_at", "participants"})
        content["participants"] = [
            p.model_dump() for p in sorted(self.participants, key=lambda p: p.employee_code)
        ]
        raw = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def invalid_constant(_value: str) -> None:
    raise ValueError("Nonfinite JSON number")


def parse_snapshot(raw: bytes) -> Snapshot:
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=invalid_constant
        )
        return Snapshot.model_validate(value)
    except (ValueError, RecursionError) as exc:
        raise K1Error("k1_invalid_request") from exc


class ReceiptAck(StrictModel):
    event_id: str
    campaign_id: PositiveId
    content_revision: PositiveId
    body_sha256: str
    outcome: Outcome


class RegistryAck(StrictModel):
    campaign_id: PositiveId
    content_revision: PositiveId
    content_sha256: str
    state: State
    issued_at: AckTime
    valid_until: AckTime
    participant_count: Annotated[int, Field(ge=0)]
    observed_at: AckTime


class Ack(StrictModel):
    schema_version: Literal[1] = 1
    receipt: ReceiptAck
    replayed: bool
    registry: RegistryAck
