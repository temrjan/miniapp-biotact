"""Product Pydantic schemas."""

from datetime import datetime
from decimal import Decimal
from typing import Optional, List

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProductBase(BaseModel):
    """Base product schema."""

    name: str = Field(..., min_length=1, max_length=200)
    name_uz: Optional[str] = Field(None, max_length=200)
    slug: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    description_uz: Optional[str] = None
    price: Decimal = Field(..., gt=0, decimal_places=2)
    old_price: Optional[Decimal] = Field(None, gt=0, decimal_places=2)
    currency: str = Field(default="UZS", max_length=3)
    stock_quantity: int = Field(default=0, ge=0)
    is_in_stock: bool = True
    image_url: Optional[str] = None
    images: Optional[List[str]] = Field(
        default=None,
        description="Array of image URLs for product gallery"
    )
    category_id: Optional[int] = None
    mxik: Optional[str] = Field(
        None, max_length=17, description="ИКПУ из tasnif.soliq.uz (17 символов)"
    )
    package_code: Optional[str] = Field(
        None, max_length=7, description="Код упаковки (7 символов)"
    )
    vat: Optional[int] = Field(None, ge=0, le=100, description="НДС в процентах")
    advantages: Optional[List[str]] = Field(
        default=None,
        description="Array of product benefits/advantages"
    )
    video_url: Optional[str] = Field(None, max_length=500)
    additional_info_title: Optional[str] = Field(None, max_length=200)
    additional_info_title_uz: Optional[str] = Field(None, max_length=200)
    additional_info: Optional[str] = None
    additional_info_uz: Optional[str] = None
    disclaimer: Optional[str] = None
    disclaimer_uz: Optional[str] = None
    sort_order: int = 0
    is_active: bool = True
    is_featured: bool = False

    @field_validator("images", "advantages", mode="before")
    @classmethod
    def validate_arrays(cls, v):
        if v is None:
            return None
        if not isinstance(v, list):
            raise ValueError("Must be a list")
        if len(v) > 20:
            raise ValueError("Max 20 items allowed")
        return v


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    name_uz: Optional[str] = Field(None, max_length=200)
    slug: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = None
    description_uz: Optional[str] = None
    price: Optional[Decimal] = Field(None, gt=0)
    old_price: Optional[Decimal] = Field(None, gt=0)
    currency: Optional[str] = Field(None, max_length=3)
    stock_quantity: Optional[int] = Field(None, ge=0)
    is_in_stock: Optional[bool] = None
    image_url: Optional[str] = None
    images: Optional[List[str]] = Field(
        default=None,
        description="Array of image URLs for product gallery"
    )
    category_id: Optional[int] = None
    mxik: Optional[str] = Field(None, max_length=17)
    package_code: Optional[str] = Field(None, max_length=7)
    vat: Optional[int] = Field(None, ge=0, le=100)
    advantages: Optional[List[str]] = Field(
        default=None,
        description="Array of product benefits/advantages"
    )
    video_url: Optional[str] = Field(None, max_length=500)
    additional_info_title: Optional[str] = Field(None, max_length=200)
    additional_info_title_uz: Optional[str] = Field(None, max_length=200)
    additional_info: Optional[str] = None
    additional_info_uz: Optional[str] = None
    disclaimer: Optional[str] = None
    disclaimer_uz: Optional[str] = None
    sort_order: Optional[int] = None
    is_active: Optional[bool] = None
    is_featured: Optional[bool] = None

    @field_validator("images", "advantages", mode="before")
    @classmethod
    def validate_arrays(cls, v):
        if v is None:
            return None
        if not isinstance(v, list):
            raise ValueError("Must be a list")
        if len(v) > 20:
            raise ValueError("Max 20 items allowed")
        return v


class ProductResponse(ProductBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime


class ProductLocalizedResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    slug: str
    description: Optional[str] = None
    price: Decimal
    old_price: Optional[Decimal] = None
    currency: str
    stock_quantity: int
    is_in_stock: bool
    is_purchasable: bool = True
    image_url: Optional[str] = None
    images: Optional[List[str]] = None
    category_id: Optional[int] = None
    mxik: Optional[str] = None
    package_code: Optional[str] = None
    vat: Optional[int] = None
    advantages: Optional[List[str]] = None
    video_url: Optional[str] = None
    additional_info_title: Optional[str] = None
    additional_info: Optional[str] = None
    disclaimer: Optional[str] = None
    sort_order: int
    is_active: bool
    is_featured: bool
    created_at: datetime
    updated_at: datetime


class ProductListResponse(BaseModel):
    items: List[ProductResponse]
    total: int
    page: int
    size: int
    pages: int


class ProductLocalizedListResponse(BaseModel):
    items: List[ProductLocalizedResponse]
    total: int
    page: int
    size: int
    pages: int
