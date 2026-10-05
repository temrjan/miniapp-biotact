"""Bounded strict count readback values; retained facts are the authority."""

import hashlib
import hmac
import json
import re
from datetime import date, datetime
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BeforeValidator, Field, PlainSerializer, model_validator

from src.employee_referrals.return_schemas import CountSnapshot as Snapshot
from src.employee_referrals.schemas import (
    PositiveId,
    StrictModel,
    invalid_constant,
    unique_object,
    utc_text,
    wire_time,
)

PATH = "/api/v1/internal/employee-referrals/count-readback"
MAX_REQUEST = 1024
MAX_RESPONSE = 16777216
MAX_EVENTS = 10000


def day_text(value: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Invalid day")
    date.fromisoformat(value)
    return value


def decimal(value: str) -> str:
    if not re.fullmatch(r"0|[1-9][0-9]*", value):
        raise ValueError("Invalid quantity")
    return value


def signed(value: str) -> str:
    if not re.fullmatch(r"0|-?[1-9][0-9]*", value):
        raise ValueError("Invalid quantity")
    return value


def observation_time(value: object) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z", value
    ):
        raise ValueError("Invalid observation timestamp")
    return wire_time(value)


ObservationTime = Annotated[datetime, BeforeValidator(observation_time), PlainSerializer(utc_text)]
Day = Annotated[str, AfterValidator(day_text)]
Quantity = Annotated[str, Field(max_length=64), AfterValidator(decimal)]
Signed = Annotated[str, Field(max_length=64), AfterValidator(signed)]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
One = Annotated[int, Field(strict=True, ge=1, le=1)]
Bound = Annotated[int, Field(strict=True, ge=0, le=MAX_EVENTS)]


class CountError(Exception):
    def __init__(self, code: str = "invalid_request") -> None:
        self.code = "count_" + code
        super().__init__(self.code)


class RequestBody(StrictModel):
    schema_version: One
    campaign_id: PositiveId
    day: Day


class Totals(StrictModel):
    paid_quantity: Quantity
    returned_quantity: Quantity
    net_quantity: Signed

    @model_validator(mode="after")
    def equality(self) -> Self:
        if int(self.paid_quantity) - int(self.returned_quantity) != int(self.net_quantity):
            raise ValueError("Invalid quantities")
        return self


class Delta(StrictModel):
    order_item_id: PositiveId
    quantity: PositiveId


class Event(StrictModel):
    snapshot: Snapshot
    body_sha256: Hash
    return_delta: list[Delta] | None

    @model_validator(mode="after")
    def delta(self) -> Self:
        if self.snapshot.order_version == 1:
            if self.return_delta is not None:
                raise ValueError("Invalid paid delta")
        elif not self.return_delta:
            raise ValueError("Missing return delta")
        else:
            ids = [d.order_item_id for d in self.return_delta]
            if ids != sorted(set(ids)):
                raise ValueError("Invalid delta order")
        return self


class Delivery(StrictModel):
    missing_jobs: Bound
    pending: Bound
    leased: Bound
    blocked: Bound
    acknowledged: Bound
    ack_unready: Bound


class Readback(StrictModel):
    schema_version: One
    campaign_id: Quantity
    day: Day
    source_observed_at: ObservationTime
    source_state: Literal["retained_valid", "source_gap", "inconsistent"]
    coverage_scope: Literal["retained-source-v1"]
    events: Annotated[list[Event], Field(max_length=MAX_EVENTS)]
    totals: Totals | None
    daily: Totals | None
    delivery: Delivery

    @model_validator(mode="after")
    def consistency(self) -> Self:
        if not 1 <= int(self.campaign_id) <= 9223372036854775807:
            raise ValueError("Invalid campaign")
        refs = [(e.snapshot.order_id, e.snapshot.order_version) for e in self.events]
        if refs != sorted(set(refs)) or any(
            e.snapshot.attribution.campaign_id != int(self.campaign_id) for e in self.events
        ):
            raise ValueError("Invalid event scope")
        if self.source_state == "retained_valid":
            if self.totals is None or self.daily is None or int(self.totals.net_quantity) < 0:
                raise ValueError("Invalid totals")
        elif self.totals is not None or self.daily is not None:
            raise ValueError("Invalid failure totals")
        return self


def quantity_values(paid: int, returned: int) -> dict[str, str]:
    return {
        "paid_quantity": str(paid),
        "returned_quantity": str(returned),
        "net_quantity": str(paid - returned),
    }


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode()


def signature(key: str, body: bytes, timestamp: str) -> str:
    prefix = f"biotact-count-v1\nportal-to-shop\nPOST\n{PATH}\n{timestamp}\n".encode()
    return hmac.new(bytes.fromhex(key), prefix + body, hashlib.sha256).hexdigest()


def decode(raw: bytes) -> object:
    return json.loads(
        raw.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=invalid_constant
    )
