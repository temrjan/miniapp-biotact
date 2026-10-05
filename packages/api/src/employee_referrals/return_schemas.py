"""Strict admin delta and retained K2 count values; no coercion or private errors."""

import json
import re
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, model_validator

from src.employee_referrals.schemas import (
    MAX_BIGINT,
    PositiveId,
    StrictModel,
    UuidText,
    WireTime,
    invalid_constant,
    unique_object,
)


class ReturnError(Exception):
    def __init__(self, code: str = "invalid_request") -> None:
        self.code = "referral_return_" + code
        super().__init__(self.code)


def decimal(value: str) -> str:
    if not re.fullmatch(r"[1-9][0-9]{0,18}", value) or int(value) > MAX_BIGINT:
        raise ValueError("Invalid decimal")
    return value


Decimal = Annotated[str, AfterValidator(decimal)]


class Delta(StrictModel):
    order_item_id: Decimal
    quantity: Decimal


class Intent(StrictModel):
    return_id: UuidText
    lines: list[Delta]

    @model_validator(mode="after")
    def unique(self) -> Self:
        if not self.lines or len({line.order_item_id for line in self.lines}) != len(self.lines):
            raise ValueError("Invalid lines")
        return self

    def normalized(self) -> list[dict[str, str]]:
        return [
            line.model_dump()
            for line in sorted(self.lines, key=lambda line: int(line.order_item_id))
        ]


class Attribution(StrictModel):
    campaign_id: PositiveId
    employee_code: UuidText
    product_id: PositiveId
    content_revision: PositiveId
    captured_at: WireTime


class PaidLine(StrictModel):
    order_item_id: PositiveId
    product_id: PositiveId
    quantity: PositiveId


class ReturnLine(PaidLine):
    returned_quantity: Annotated[int, Field(strict=True, ge=0, le=MAX_BIGINT)]

    @model_validator(mode="after")
    def bounds(self) -> Self:
        if self.returned_quantity > self.quantity:
            raise ValueError("Invalid returned quantity")
        return self


class CountSnapshot(StrictModel):
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    event_id: UuidText
    event_type: Literal["sale_paid", "sale_refunded"]
    source: Literal["biotact-shop"]
    order_id: PositiveId
    order_version: PositiveId
    source_position: PositiveId
    occurred_at: WireTime
    source_recorded_at: WireTime
    attribution: Attribution
    lines: list[PaidLine | ReturnLine]

    @model_validator(mode="after")
    def consistency(self) -> Self:
        ids = [line.order_item_id for line in self.lines]
        if (
            not ids
            or ids != sorted(set(ids))
            or any(line.product_id != self.attribution.product_id for line in self.lines)
        ):
            raise ValueError("Invalid lines")
        if self.event_type == "sale_paid":
            if self.order_version != 1 or any(isinstance(line, ReturnLine) for line in self.lines):
                raise ValueError("Invalid paid snapshot")
        elif (
            self.order_version < 2
            or not all(isinstance(line, ReturnLine) for line in self.lines)
            or not any(self.returned().values())
        ):
            raise ValueError("Invalid returned snapshot")
        return self

    def identity(self) -> dict[str, object]:
        return {
            "attribution": self.attribution.model_dump(mode="json"),
            "lines": [line.model_dump(exclude={"returned_quantity"}) for line in self.lines],
        }

    def returned(self) -> dict[int, int]:
        return {line.order_item_id: getattr(line, "returned_quantity", 0) for line in self.lines}


def load_json(raw: bytes) -> object:
    if not 1 <= len(raw) <= 1048576:
        raise ValueError("Invalid size")
    return json.loads(
        raw.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=invalid_constant
    )


def parse_intent(raw: bytes) -> Intent:
    try:
        return Intent.model_validate(load_json(raw))
    except (ValueError, RecursionError) as exc:
        raise ReturnError() from exc
