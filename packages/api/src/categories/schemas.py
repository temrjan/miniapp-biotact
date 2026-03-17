"""Category Pydantic schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CategoryBase(BaseModel):
    """Base category schema."""
    name: str = Field(..., min_length=1, max_length=100)
    name_uz: str | None = Field(None, max_length=100)
    slug: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    description_uz: str | None = None
    image_url: str | None = None
    sort_order: int = 0
    is_active: bool = True
    is_purchasable: bool = True


class CategoryCreate(CategoryBase):
    """Schema for creating a category."""
    pass


class CategoryUpdate(BaseModel):
    """Schema for updating a category."""
    name: str | None = Field(None, min_length=1, max_length=100)
    name_uz: str | None = Field(None, max_length=100)
    slug: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    description_uz: str | None = None
    image_url: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None
    is_purchasable: bool | None = None


class CategoryResponse(CategoryBase):
    """Category response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class CategoryListResponse(BaseModel):
    """Paginated category list response."""
    items: list[CategoryResponse]
    total: int
    page: int
    size: int
    pages: int
