"""Banner Pydantic schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BannerBase(BaseModel):
    """Base banner schema."""

    title: str
    title_uz: str | None = None
    subtitle: str | None = None
    subtitle_uz: str | None = None
    image_url: str | None = None
    gradient: str | None = None
    link_url: str | None = None
    sort_order: int = 0
    is_active: bool = True


class BannerCreate(BannerBase):
    """Schema for creating a banner."""

    pass


class BannerUpdate(BaseModel):
    """Schema for updating a banner."""

    title: str | None = None
    title_uz: str | None = None
    subtitle: str | None = None
    subtitle_uz: str | None = None
    image_url: str | None = None
    gradient: str | None = None
    link_url: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None


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
