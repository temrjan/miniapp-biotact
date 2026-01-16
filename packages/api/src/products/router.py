"""Product API router."""

from typing import Annotated, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.language import CurrentLanguage, Language
from src.database import get_db
from src.products import service
from src.products.models import Product
from src.products.schemas import (
    ProductCreate,
    ProductListResponse,
    ProductLocalizedListResponse,
    ProductLocalizedResponse,
    ProductResponse,
    ProductUpdate,
)

router = APIRouter()


# Type Aliases
DbSession = Annotated[AsyncSession, Depends(get_db)]


def localize_product(product: Product, lang: Language) -> dict:
    """Create localized product dict based on language.

    Falls back to Russian if translation not available.
    JSONB fields (images, advantages) are automatically converted to lists by SQLAlchemy.
    """
    return {
        "id": product.id,
        "name": product.name_uz if lang == "uz" and product.name_uz else product.name,
        "slug": product.slug,
        "description": (
            product.description_uz
            if lang == "uz" and product.description_uz
            else product.description
        ),
        "price": product.price,
        "old_price": product.old_price,
        "currency": product.currency,
        "stock_quantity": product.stock_quantity,
        "is_in_stock": product.is_in_stock,
        "image_url": product.image_url,
        # JSONB automatically converts to List[str] - no json.loads needed!
        "images": product.images,
        "category_id": product.category_id,
        "mxik": product.mxik,
        "package_code": product.package_code,
        "vat": product.vat,
        # JSONB automatically converts to List[str] - no json.loads needed!
        "advantages": product.advantages,
        "video_url": product.video_url,
        "additional_info_title": (
            product.additional_info_title_uz
            if lang == "uz" and product.additional_info_title_uz
            else product.additional_info_title
        ),
        "additional_info": (
            product.additional_info_uz
            if lang == "uz" and product.additional_info_uz
            else product.additional_info
        ),
        "disclaimer": (
            product.disclaimer_uz
            if lang == "uz" and product.disclaimer_uz
            else product.disclaimer
        ),
        "sort_order": product.sort_order,
        "is_active": product.is_active,
        "is_featured": product.is_featured,
        "created_at": product.created_at,
        "updated_at": product.updated_at,
    }


@router.get("", response_model=Union[ProductListResponse, ProductLocalizedListResponse])
async def get_products(
    db: DbSession,
    lang: CurrentLanguage,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    category_id: Optional[int] = Query(None),
    active_only: bool = Query(True),
    featured_only: bool = Query(False),
    search: Optional[str] = Query(None, min_length=1),
    include_all_translations: bool = Query(False, description="Return all translation fields (for admin)"),
):
    """Get paginated list of products (localized by Accept-Language or all fields for admin)."""
    result = await service.get_products_paginated(
        db,
        page=page,
        size=size,
        category_id=category_id,
        active_only=active_only,
        featured_only=featured_only,
        search=search,
    )

    # If admin panel needs all translations, return full ProductResponse objects
    if include_all_translations:
        return ProductListResponse(
            items=result.items,
            total=result.total,
            page=result.page,
            size=result.size,
            pages=result.pages,
        )

    # Otherwise return localized responses for end users
    localized_items = [
        ProductLocalizedResponse.model_validate(localize_product(item, lang))
        for item in result.items
    ]

    return ProductLocalizedListResponse(
        items=localized_items,
        total=result.total,
        page=result.page,
        size=result.size,
        pages=result.pages,
    )


@router.get("/{product_id}", response_model=Union[ProductResponse, ProductLocalizedResponse])
async def get_product(
    product_id: int,
    db: DbSession,
    lang: CurrentLanguage,
    include_all_translations: bool = Query(False, description="Return all translation fields (for admin)"),
):
    """Get product by ID (localized by Accept-Language or all fields for admin)."""
    product = await service.get_product_by_id(db, product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    if include_all_translations:
        return ProductResponse.model_validate(product)

    return ProductLocalizedResponse.model_validate(localize_product(product, lang))


@router.get("/slug/{slug}", response_model=Union[ProductResponse, ProductLocalizedResponse])
async def get_product_by_slug(
    slug: str,
    db: DbSession,
    lang: CurrentLanguage,
    include_all_translations: bool = Query(False, description="Return all translation fields (for admin)"),
):
    """Get product by slug (localized by Accept-Language or all fields for admin)."""
    product = await service.get_product_by_slug(db, slug)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )

    if include_all_translations:
        return ProductResponse.model_validate(product)

    return ProductLocalizedResponse.model_validate(localize_product(product, lang))


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    product_data: ProductCreate,
    db: DbSession,
):
    """Create new product."""
    existing = await service.get_product_by_slug(db, product_data.slug)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Product with this slug already exists",
        )
    return await service.create_product(db, product_data)


@router.patch("/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: int,
    update_data: ProductUpdate,
    db: DbSession,
):
    """Update product."""
    product = await service.update_product(db, product_id, update_data)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )
    return product


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: int,
    db: DbSession,
):
    """Delete product."""
    deleted = await service.delete_product(db, product_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found",
        )


from pydantic import BaseModel


class ReorderItem(BaseModel):
    id: int
    sort_order: int


class ReorderRequest(BaseModel):
    items: list[ReorderItem]


@router.post("/reorder", status_code=status.HTTP_200_OK)
async def reorder_products(
    request: ReorderRequest,
    db: DbSession,
):
    """Update sort_order for multiple products."""
    for item in request.items:
        await service.update_product_sort_order(db, item.id, item.sort_order)
    await db.commit()
    return {"status": "ok", "updated": len(request.items)}
