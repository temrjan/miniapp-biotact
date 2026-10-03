"""Strict snake_case HTTP boundary; the portal owns campaign semantics."""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

State = Literal["draft", "published", "ended"]
PositiveId = Annotated[int, Field(strict=True, gt=0)]


def _rfc3339(value: str) -> str:
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]+)?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])",
        value,
    ):
        raise ValueError("Expected an aware RFC 3339 timestamp")
    datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
    return value


Timestamp = Annotated[str, AfterValidator(_rfc3339)]


class BoundaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class DraftInput(BoundaryModel):
    name: str = Field(max_length=200)
    product_id: PositiveId | None = None
    image_url: str | None = Field(default=None, max_length=500)
    description: str | None = None
    terms: str | None = None
    start_at: Timestamp | None = None
    end_at: Timestamp | None = None


class EditInput(BoundaryModel):
    expected_updated_at: Timestamp
    name: str | None = Field(default=None, max_length=200)
    product_id: PositiveId | None = None
    image_url: str | None = Field(default=None, max_length=500)
    description: str | None = None
    terms: str | None = None
    start_at: Timestamp | None = None
    end_at: Timestamp | None = None

    @model_validator(mode="after")
    def supplied_edit(self) -> Self:
        if not self.model_fields_set - {"expected_updated_at"}:
            raise ValueError("An editable field is required")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Name cannot be null")
        return self


class TransitionInput(BoundaryModel):
    expected_updated_at: Timestamp


class CampaignRecord(BoundaryModel):
    id: PositiveId
    name: str
    product_id: PositiveId | None
    image_url: str | None
    description: str | None
    terms: str | None
    state: State
    start_at: Timestamp | None
    end_at: Timestamp | None
    published_at: Timestamp | None
    ended_at: Timestamp | None
    created_at: Timestamp
    updated_at: Timestamp


class CampaignPage(BoundaryModel):
    items: list[CampaignRecord]
    total: int = Field(ge=0)
    page: PositiveId
    size: int = Field(ge=1, le=100)
    pages: int = Field(ge=0)


ErrorCode = Literal[
    "invalid_admin_auth",
    "admin_forbidden",
    "invalid_request",
    "campaign_not_found",
    "campaign_changed",
    "invalid_campaign_state",
    "immutable_campaign",
    "campaign_overlap",
    "invalid_campaign_dates",
    "incomplete_campaign",
    "invalid_image_url",
    "product_not_found",
    "product_inactive",
    "product_not_purchasable",
    "product_out_of_stock",
    "shop_unavailable",
    "service_unavailable",
    "campaign_service_unavailable",
    "campaign_result_unknown",
]


class CampaignError(Exception):
    def __init__(self, code: ErrorCode) -> None:
        self.code = code
        super().__init__(code)


class ErrorDetail(BoundaryModel):
    code: ErrorCode
    message: str


class ErrorResponse(BoundaryModel):
    detail: ErrorDetail


@dataclass
class WriteOutcome:
    started: bool = False


def _bigint(value: str) -> str:
    if not re.fullmatch(r"[1-9][0-9]{0,18}", value) or int(value) > 9223372036854775807:
        raise ValueError("Invalid bigint")
    return value


def _sync_time(value: str) -> str:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z", value):
        raise ValueError("Invalid UTC instant")
    datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


Bigint = Annotated[str, AfterValidator(_bigint)]
SyncTime = Annotated[str, AfterValidator(_sync_time)]


class CampaignSync(BoundaryModel):
    campaign_id: Bigint
    campaign_state: State
    sender_enabled: bool
    status: Literal["not_published", "disabled", "pending", "confirmed", "expired", "blocked"]
    desired_revision: Bigint | None
    confirmed_revision: Bigint | None
    participant_count: int = Field(ge=0, le=9007199254740991)
    confirmed_at: SyncTime | None
    valid_until: SyncTime | None
    stop_confirmed: bool
    stop_deadline: SyncTime | None
    pending_count: int = Field(ge=0, le=1)
    blocked_count: int = Field(ge=0, le=1)
    oldest_pending_at: SyncTime | None
    last_error_code: (
        Literal[
            "configuration",
            "clock",
            "transport",
            "protocol",
            "authentication",
            "conflict",
            "payload_limit",
            "receiver_disabled",
            "invalid_request",
            "attempt_limit",
            "database",
            "storage",
        ]
        | None
    )
    observed_at: SyncTime
