"""Category service layer."""

from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.categories.models import Category
from src.categories.schemas import CategoryCreate, CategoryUpdate, CategoryListResponse


async def get_category_by_id(
    db: AsyncSession,
    category_id: int,
) -> Optional[Category]:
    """Get category by ID."""
    result = await db.execute(
        select(Category).where(Category.id == category_id)
    )
    return result.scalar_one_or_none()


async def get_category_by_slug(
    db: AsyncSession,
    slug: str,
) -> Optional[Category]:
    """Get category by slug."""
    result = await db.execute(
        select(Category).where(Category.slug == slug)
    )
    return result.scalar_one_or_none()


async def create_category(
    db: AsyncSession,
    category_data: CategoryCreate,
) -> Category:
    """Create new category."""
    category = Category(**category_data.model_dump())
    db.add(category)
    await db.flush()
    await db.refresh(category)
    return category


async def update_category(
    db: AsyncSession,
    category_id: int,
    update_data: CategoryUpdate,
) -> Optional[Category]:
    """Update category."""
    category = await get_category_by_id(db, category_id)
    if not category:
        return None

    update_dict = update_data.model_dump(exclude_unset=True)
    for field, value in update_dict.items():
        setattr(category, field, value)

    await db.flush()
    await db.refresh(category)
    return category


async def delete_category(
    db: AsyncSession,
    category_id: int,
) -> bool:
    """Delete category."""
    category = await get_category_by_id(db, category_id)
    if not category:
        return False

    await db.delete(category)
    return True


async def get_categories_paginated(
    db: AsyncSession,
    page: int = 1,
    size: int = 20,
    active_only: bool = True,
) -> CategoryListResponse:
    """Get paginated list of categories."""
    query = select(Category)
    
    if active_only:
        query = query.where(Category.is_active == True)
    
    # Count total
    count_result = await db.execute(
        select(func.count(Category.id)).where(Category.is_active == True) if active_only
        else select(func.count(Category.id))
    )
    total = count_result.scalar_one()

    # Get items
    offset = (page - 1) * size
    result = await db.execute(
        query
        .order_by(Category.sort_order, Category.name)
        .offset(offset)
        .limit(size)
    )
    items = list(result.scalars().all())

    # Calculate pages
    pages = (total + size - 1) // size if total > 0 else 1

    return CategoryListResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=pages,
    )
