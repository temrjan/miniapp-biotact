"""Exact administrator result contract, separate from campaign metadata."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, Field, model_validator

from src.campaigns.schemas import BoundaryModel
from src.campaigns.schemas import _sync_time as ack_time


def canonical_uuid(value: str) -> str:
    parsed = UUID(value)
    if parsed.version != 4 or parsed.variant != "specified in RFC 4122" or str(parsed) != value:
        raise ValueError("Invalid UUID")
    return value


def scalar_name(value: str) -> str:
    if not value.strip() or any(ord(c) == 0 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise ValueError("Invalid name")
    return value


Quantity = Annotated[str, Field(max_length=64, pattern=r"^(0|[1-9][0-9]*)$")]

MAX_BODY = 2 * 1024 * 1024
MAX_ROWS = 2000
PRIORITY = {"observed": 0, "incomplete": 1, "inconsistent": 2, "unavailable": 3}
ItemState = Literal["observed", "incomplete", "inconsistent", "unavailable"]


def campaign_text(value: str) -> str:
    if not value.isascii() or not value.isdecimal() or value.startswith("0"):
        raise ValueError("Invalid campaign")
    if len(value) > 19 or not 1 <= int(value) <= 9223372036854775807:
        raise ValueError("Invalid campaign")
    return value


def observed_text(value: str) -> str:
    ack_time(value)
    return value


class Totals(BoundaryModel):
    paid_quantity: Quantity
    returned_quantity: Quantity
    net_quantity: Quantity

    @model_validator(mode="after")
    def arithmetic(self) -> Self:
        if int(self.returned_quantity) > int(self.paid_quantity) or int(self.net_quantity) != int(
            self.paid_quantity
        ) - int(self.returned_quantity):
            raise ValueError("Invalid totals")
        return self


class ResultRow(BoundaryModel):
    employee_code: Annotated[str, AfterValidator(canonical_uuid)]
    employee_display_name: Annotated[str, Field(max_length=161), AfterValidator(scalar_name)]
    state: ItemState
    totals: Totals | None

    @model_validator(mode="after")
    def state_totals(self) -> Self:
        if (self.state == "observed") != (self.totals is not None):
            raise ValueError("Invalid row state")
        return self


class CampaignResults(BoundaryModel):
    schema_version: Annotated[int, Field(ge=1, le=1)]
    campaign_id: Annotated[str, AfterValidator(campaign_text)]
    campaign_state: Literal["draft", "published", "ended"]
    state: Literal["preparing", "observed", "incomplete", "inconsistent", "unavailable"]
    coverage_scope: Literal["retained-source-v1"]
    observed_at: Annotated[str, AfterValidator(observed_text)]
    items: Annotated[list[ResultRow], Field(max_length=MAX_ROWS)]
    totals: Totals | None

    @model_validator(mode="after")
    def consistency(self) -> Self:
        codes = [row.employee_code for row in self.items]
        if codes != sorted(set(codes)):
            raise ValueError("Invalid roster")
        if self.state == "preparing":
            if self.items or self.totals is not None:
                raise ValueError("Invalid preparing report")
            return self
        state = max((r.state for r in self.items), key=PRIORITY.__getitem__, default="observed")
        if self.state != state:
            raise ValueError("Invalid report state")
        if state != "observed":
            if self.totals is not None:
                raise ValueError("Invalid failure totals")
        else:
            values = [r.totals for r in self.items if r.totals is not None]
            expected = {
                key: str(sum(int(getattr(t, key)) for t in values)) for key in Totals.model_fields
            }
            if self.totals is None or self.totals.model_dump() != expected:
                raise ValueError("Invalid aggregate")
        return self
