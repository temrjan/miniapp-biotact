"""Broadcast Pydantic schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BroadcastCreate(BaseModel):
    """Schema for creating a broadcast."""

    title: str = Field(..., min_length=1, max_length=255)
    text: str = Field(..., min_length=1, max_length=4096)
    photo_url: str | None = None
    video_url: str | None = None
    button_text: str | None = Field(None, max_length=64)
    button_url: str | None = None

    @model_validator(mode="after")
    def validate_media_exclusive(self) -> "BroadcastCreate":
        """photo_url and video_url are mutually exclusive."""
        if self.photo_url and self.video_url:
            raise ValueError("photo_url and video_url cannot be set at the same time")
        return self

    @model_validator(mode="after")
    def validate_button_pair(self) -> "BroadcastCreate":
        """Both button_text and button_url must be provided together."""
        if bool(self.button_text) != bool(self.button_url):
            raise ValueError("button_text and button_url must be provided together")
        return self

    @model_validator(mode="after")
    def validate_caption_length(self) -> "BroadcastCreate":
        """Caption limited to 1024 chars when photo or video is present."""
        if (self.photo_url or self.video_url) and len(self.text) > 1024:
            raise ValueError("Text must be <= 1024 characters when media is attached")
        return self


class BroadcastResponse(BaseModel):
    """Broadcast response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    text: str
    photo_url: str | None = None
    video_url: str | None = None
    button_text: str | None = None
    button_url: str | None = None
    status: str
    total_users: int
    sent_count: int
    failed_count: int
    blocked_count: int
    created_by: int | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class BroadcastListResponse(BaseModel):
    """Paginated broadcast list."""

    items: list[BroadcastResponse]
    total: int
    page: int
    size: int
    pages: int
