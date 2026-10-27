"""Strict request bodies of the website guest checkout (G1, spec S2 sections 5-6)."""

import re
import unicodedata
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

MAX_PRODUCT_ID = 2147483647  # PostgreSQL ``integer``; a larger id is a 422, never a database error.
PHONE_SYNTAX = re.compile(r"\+?[0-9 ().-]+")
FINGERPRINT = re.compile(r"[0-9a-f]{64}")
IDEMPOTENCY_KEY = re.compile(r"[0-9a-f]{32}")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GuestLine(Strict):
    product_id: Annotated[StrictInt, Field(ge=1, le=MAX_PRODUCT_ID)]
    quantity: Annotated[StrictInt, Field(ge=1, le=99)]
    size: Annotated[StrictStr, Field(min_length=1, max_length=3)] | None


class GuestQuoteRequest(Strict):
    # No maximum here: more than 20 lines is ``order_too_large`` (OC-03), not a validation error;
    # the 4096-byte body cap bounds the list.
    items: list[GuestLine] = Field(min_length=1)


def _controls(value: str, allowed: str = "") -> bool:
    return any(unicodedata.category(c) == "Cc" and c not in allowed for c in value)


class GuestOrderRequest(GuestQuoteRequest):
    quote_fingerprint: StrictStr
    name: StrictStr
    phone: StrictStr
    address: StrictStr
    accept_terms: StrictBool
    idempotency_key: StrictStr

    @field_validator("quote_fingerprint")
    @classmethod
    def fingerprint(cls, value: str) -> str:
        if not FINGERPRINT.fullmatch(value):
            raise ValueError("invalid")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def key(cls, value: str) -> str:
        if not IDEMPOTENCY_KEY.fullmatch(value):
            raise ValueError("invalid")
        return value

    @field_validator("name")
    @classmethod
    def trimmed_name(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 100 or _controls(value):
            raise ValueError("invalid")
        return value

    @field_validator("phone")
    @classmethod
    def phone_syntax(cls, value: str) -> str:
        value = value.strip()
        digits = sum(c in "0123456789" for c in value)
        if len(value) > 20 or not PHONE_SYNTAX.fullmatch(value) or not 9 <= digits <= 15:
            raise ValueError("invalid")
        return value

    @field_validator("address")
    @classmethod
    def trimmed_address(cls, value: str) -> str:
        value = value.strip()
        if not 1 <= len(value) <= 300 or _controls(value, "\r\n"):
            raise ValueError("invalid")
        return value

    @field_validator("accept_terms")
    @classmethod
    def accepted(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("terms")
        return value
