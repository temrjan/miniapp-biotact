"""Category Pydantic schemas."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class CategoryBase(BaseModel):
    """Base category schema."""
    name: str = Field(..., min_length=1, max_length=100)
    name_uz: Optional[str] = Field(None, max_length=100)
    slug: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    description_uz: Optional[str] = None
    image_url: Optional[str] = None
    sort_order: int = 0
    is_active: bool = True
    is_purchasable: bool = True


class CategoryCreate(CategoryBase):
    """Schema for creating a category."""
    pass


class CategoryUpdate(BaseModel):
    """Schema for updating a category."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    name_uz: Optional[str] = Field(None, max_length=100)
    slug: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    description_uz: Optional[str] = None
    image_url: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None
    is_purchasable: Optional[bool] = None


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
