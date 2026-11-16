"""Banner Pydantic schemas."""

from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

_MEDIA_URL_MAX_LENGTH = 500
_MEDIA_URL_FIELDS = ("image_url", "image_mobile_url", "video_url", "video_mobile_url")


def _validate_https_url(value: str | None) -> str | None:
    """Validate an optional media URL as an absolute https URL without credentials."""
    if value is None:
        return None
    if len(value) > _MEDIA_URL_MAX_LENGTH:
        raise ValueError(f"URL must be at most {_MEDIA_URL_MAX_LENGTH} characters")
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("URL must not contain whitespace or control characters")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("URL must be a valid absolute https URL") from exc
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not hostname
        or "@" in parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("URL must be an absolute https URL without credentials")
    return value


def _validate_non_blank_title(value: str | None) -> str | None:
    """Ensure a provided banner title is a non-blank string."""
    if value is None or not value.strip():
        raise ValueError("Title must not be blank")
    return value


class BannerBase(BaseModel):
    """Base banner schema."""

    title: str
    title_uz: str | None = None
    subtitle: str | None = None
    subtitle_uz: str | None = None
    image_url: str | None = None
    image_mobile_url: str | None = None
    video_url: str | None = None
    video_mobile_url: str | None = None
    gradient: str | None = None
    link_url: str | None = None
    sort_order: int = 0
    is_active: bool = True


class BannerCreate(BannerBase):
    """Schema for creating a banner."""

    title: str = Field(..., min_length=1, max_length=200)
    title_uz: str | None = Field(default=None, max_length=200)
    subtitle: str | None = Field(default=None, max_length=500)
    subtitle_uz: str | None = Field(default=None, max_length=500)

    @field_validator("title")
    @classmethod
    def _check_title(cls, value: str) -> str:
        validated = _validate_non_blank_title(value)
        assert validated is not None
        return validated

    @field_validator(*_MEDIA_URL_FIELDS)
    @classmethod
    def _check_media_url(cls, value: str | None) -> str | None:
        return _validate_https_url(value)


class BannerUpdate(BaseModel):
    """Schema for updating a banner."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    title_uz: str | None = Field(default=None, max_length=200)
    subtitle: str | None = Field(default=None, max_length=500)
    subtitle_uz: str | None = Field(default=None, max_length=500)
    image_url: str | None = None
    image_mobile_url: str | None = None
    video_url: str | None = None
    video_mobile_url: str | None = None
    gradient: str | None = None
    link_url: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None

    @field_validator("title")
    @classmethod
    def _check_title(cls, value: str | None) -> str | None:
        return _validate_non_blank_title(value)

    @field_validator(*_MEDIA_URL_FIELDS)
    @classmethod
    def _check_media_url(cls, value: str | None) -> str | None:
        return _validate_https_url(value)


class Banner(BannerBase):
    """Banner response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class BannerList(BaseModel):
    """Paginated banner list response."""

    items: list[Banner]
    total: int


class SettingsBase(BaseModel):
    """Base settings schema."""

    key: str
    value: str
    description: str | None = None


class SettingsUpdate(BaseModel):
    """Schema for updating settings."""

    value: str


class Settings(SettingsBase):
    """Settings response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    updated_at: datetime
