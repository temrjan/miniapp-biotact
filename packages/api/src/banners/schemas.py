"""Banner Pydantic schemas."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class BannerBase(BaseModel):
    """Base banner schema."""
    title: str
    title_uz: Optional[str] = None
    subtitle: Optional[str] = None
    subtitle_uz: Optional[str] = None
    image_url: Optional[str] = None
    gradient: Optional[str] = None
    link_url: Optional[str] = None
    sort_order: int = 0
    is_active: bool = True


class BannerCreate(BannerBase):
    """Schema for creating a banner."""
    pass


class BannerUpdate(BaseModel):
    """Schema for updating a banner."""
    title: Optional[str] = None
    title_uz: Optional[str] = None
    subtitle: Optional[str] = None
    subtitle_uz: Optional[str] = None
    image_url: Optional[str] = None
    gradient: Optional[str] = None
    link_url: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None


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
    description: Optional[str] = None


class SettingsUpdate(BaseModel):
    """Schema for updating settings."""
    value: str


class Settings(SettingsBase):
    """Settings response schema."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    updated_at: datetime
