"""Product Pydantic schemas."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProductBase(BaseModel):
    """Base product schema."""

    name: str = Field(..., min_length=1, max_length=200)
    name_uz: str | None = Field(None, max_length=200)
    slug: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    description_uz: str | None = None
    price: Decimal = Field(..., gt=0, decimal_places=2)
    old_price: Decimal | None = Field(None, gt=0, decimal_places=2)
    currency: str = Field(default="UZS", max_length=3)
    stock_quantity: int = Field(default=0, ge=0)
    is_in_stock: bool = True
    image_url: str | None = None
    images: list[str] | None = Field(
        default=None, description="Array of image URLs for product gallery"
    )
    category_id: int | None = None
    mxik: str | None = Field(
        None, max_length=17, description="ИКПУ из tasnif.soliq.uz (17 символов)"
    )
    package_code: str | None = Field(None, max_length=7, description="Код упаковки (7 символов)")
    vat: int | None = Field(None, ge=0, le=100, description="НДС в процентах")
    advantages: list[str] | None = Field(
        default=None, description="Array of product benefits/advantages"
    )
    video_url: str | None = Field(None, max_length=500)
    additional_info_title: str | None = Field(None, max_length=200)
    additional_info_title_uz: str | None = Field(None, max_length=200)
    additional_info: str | None = None
    additional_info_uz: str | None = None
    disclaimer: str | None = None
    disclaimer_uz: str | None = None
    sort_order: int = 0
    is_active: bool = True
    is_featured: bool = False

    @field_validator("images", "advantages", mode="before")
    @classmethod
    def validate_arrays(cls, v: list[str] | None) -> list[str] | None:
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
    name: str | None = Field(None, min_length=1, max_length=200)
    name_uz: str | None = Field(None, max_length=200)
    slug: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    description_uz: str | None = None
    price: Decimal | None = Field(None, gt=0)
    old_price: Decimal | None = Field(None, gt=0)
    currency: str | None = Field(None, max_length=3)
    stock_quantity: int | None = Field(None, ge=0)
    is_in_stock: bool | None = None
    image_url: str | None = None
    images: list[str] | None = Field(
        default=None, description="Array of image URLs for product gallery"
    )
    category_id: int | None = None
    mxik: str | None = Field(None, max_length=17)
    package_code: str | None = Field(None, max_length=7)
    vat: int | None = Field(None, ge=0, le=100)
    advantages: list[str] | None = Field(
        default=None, description="Array of product benefits/advantages"
    )
    video_url: str | None = Field(None, max_length=500)
    additional_info_title: str | None = Field(None, max_length=200)
    additional_info_title_uz: str | None = Field(None, max_length=200)
    additional_info: str | None = None
    additional_info_uz: str | None = None
    disclaimer: str | None = None
    disclaimer_uz: str | None = None
    sort_order: int | None = None
    is_active: bool | None = None
    is_featured: bool | None = None

    @field_validator("images", "advantages", mode="before")
    @classmethod
    def validate_arrays(cls, v: list[str] | None) -> list[str] | None:
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
    description: str | None = None
    price: Decimal
    old_price: Decimal | None = None
    currency: str
    stock_quantity: int
    is_in_stock: bool
    is_purchasable: bool = True
    image_url: str | None = None
    images: list[str] | None = None
    category_id: int | None = None
    mxik: str | None = None
    package_code: str | None = None
    vat: int | None = None
    advantages: list[str] | None = None
    video_url: str | None = None
    additional_info_title: str | None = None
    additional_info: str | None = None
    disclaimer: str | None = None
    sort_order: int
    is_active: bool
    is_featured: bool
    created_at: datetime
    updated_at: datetime


class ProductListResponse(BaseModel):
    items: list[ProductResponse]
    total: int
    page: int
    size: int
    pages: int


class ProductLocalizedListResponse(BaseModel):
    items: list[ProductLocalizedResponse]
    total: int
    page: int
    size: int
    pages: int
