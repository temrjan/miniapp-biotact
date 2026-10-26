"""Category API router."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import verify_admin_jwt
from src.categories import service
from src.categories.models import Category
from src.categories.schemas import (
    CategoryCreate,
    CategoryListResponse,
    CategoryResponse,
    CategoryUpdate,
)
from src.common.language import CurrentLanguage, Language
from src.database import get_db
from src.products.cache import LocalizedCatalogueReadRoute

router = APIRouter(route_class=LocalizedCatalogueReadRoute)


# Type Aliases
DbSession = Annotated[AsyncSession, Depends(get_db)]


def localize_category(category: Category, lang: Language) -> dict[str, Any]:
    """Create localized category dict based on language.

    Falls back to Russian if translation not available.
    """
    return {
        "id": category.id,
        "name": category.name_uz if lang == "uz" and category.name_uz else category.name,
        "name_uz": category.name_uz,
        "slug": category.slug,
        "description": (
            category.description_uz
            if lang == "uz" and category.description_uz
            else category.description
        ),
        "description_uz": category.description_uz,
        "image_url": category.image_url,
        "sort_order": category.sort_order,
        "is_active": category.is_active,
        "is_purchasable": category.is_purchasable,
        "created_at": category.created_at,
        "updated_at": category.updated_at,
    }


@router.get("", response_model=CategoryListResponse)
async def get_categories(
    db: DbSession,
    lang: CurrentLanguage,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    active_only: bool = Query(True),
) -> CategoryListResponse:
    """Get paginated list of categories (localized by Accept-Language)."""
    result = await service.get_categories_paginated(
        db, page=page, size=size, active_only=active_only
    )

    # Localize categories
    localized_items = [
        CategoryResponse.model_validate(localize_category(cat, lang))  # type: ignore[arg-type]
        for cat in result.items
    ]

    return CategoryListResponse(
        items=localized_items,
        total=result.total,
        page=result.page,
        size=result.size,
        pages=result.pages,
    )


@router.get("/{category_id}", response_model=CategoryResponse)
async def get_category(
    category_id: int,
    db: DbSession,
    lang: CurrentLanguage,
) -> dict[str, Any]:
    """Get category by ID (localized by Accept-Language)."""
    category = await service.get_category_by_id(db, category_id)
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found",
        )
    return localize_category(category, lang)


@router.get("/slug/{slug}", response_model=CategoryResponse)
async def get_category_by_slug(
    slug: str,
    db: DbSession,
    lang: CurrentLanguage,
) -> dict[str, Any]:
    """Get category by slug (localized by Accept-Language)."""
    category = await service.get_category_by_slug(db, slug)
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found",
        )
    return localize_category(category, lang)


@router.post(
    "",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_admin_jwt)],
)
async def create_category(
    category_data: CategoryCreate,
    db: DbSession,
) -> Category:
    """Create new category."""
    # Check if slug already exists
    existing = await service.get_category_by_slug(db, category_data.slug)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Category with this slug already exists",
        )
    return await service.create_category(db, category_data)


@router.patch(
    "/{category_id}", response_model=CategoryResponse, dependencies=[Depends(verify_admin_jwt)]
)
async def update_category(
    category_id: int,
    update_data: CategoryUpdate,
    db: DbSession,
) -> Category:
    """Update category."""
    category = await service.update_category(db, category_id, update_data)
    if not category:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found",
        )
    return category


@router.delete(
    "/{category_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(verify_admin_jwt)],
)
async def delete_category(
    category_id: int,
    db: DbSession,
) -> None:
    """Delete category."""
    deleted = await service.delete_category(db, category_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Category not found",
        )
