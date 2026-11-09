"""Response of the "my discount" read (spec event-qr-discount S3, R-20)."""

from datetime import datetime

from pydantic import BaseModel, Field


def _absent(value: object) -> bool:
    return value is None


class MyPromotion(BaseModel):
    """``{"active": false}`` or the selected grant's title, percent and end instant."""

    active: bool
    title: str | None = Field(None, exclude_if=_absent)
    percent: int | None = Field(None, exclude_if=_absent)
    ends_at: datetime | None = Field(None, exclude_if=_absent)
