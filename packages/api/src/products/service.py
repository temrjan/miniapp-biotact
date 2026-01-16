"""Product service layer."""

from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.products.models import Product
from src.products.schemas import ProductCreate, ProductUpdate, ProductListResponse


async def get_product_by_id(
    db: AsyncSession,
    product_id: int,
) -> Optional[Product]:
    """Get product by ID."""
    result = await db.execute(
        select(Product).where(Product.id == product_id)
    )
    return result.scalar_one_or_none()


async def get_product_by_slug(
    db: AsyncSession,
    slug: str,
) -> Optional[Product]:
    """Get product by slug."""
    result = await db.execute(
        select(Product).where(Product.slug == slug)
    )
    return result.scalar_one_or_none()


async def create_product(
    db: AsyncSession,
    product_data: ProductCreate,
) -> Product:
    """Create new product.
    
    JSONB fields (images, advantages) are automatically converted by SQLAlchemy.
    No need for json.dumps anymore!
    """
    data = product_data.model_dump()
    # JSONB automatically handles Python lists - no json.dumps needed!
    # SQLAlchemy will convert List[str] -> JSONB automatically
    product = Product(**data)
    db.add(product)
    await db.flush()
    await db.refresh(product)
    return product


async def update_product(
    db: AsyncSession,
    product_id: int,
    update_data: ProductUpdate,
) -> Optional[Product]:
    """Update product.
    
    JSONB fields (images, advantages) are automatically converted by SQLAlchemy.
    No need for json.dumps anymore!
    """
    product = await get_product_by_id(db, product_id)
    if not product:
        return None

    update_dict = update_data.model_dump(exclude_unset=True)
    # JSONB automatically handles Python lists - no json.dumps needed!
    # SQLAlchemy will convert List[str] -> JSONB automatically
    for field, value in update_dict.items():
        setattr(product, field, value)

    await db.flush()
    await db.refresh(product)
    return product


async def delete_product(
    db: AsyncSession,
    product_id: int,
) -> bool:
    """Delete product."""
    product = await get_product_by_id(db, product_id)
    if not product:
        return False

    await db.delete(product)
    return True


async def get_products_paginated(
    db: AsyncSession,
    page: int = 1,
    size: int = 20,
    category_id: Optional[int] = None,
    active_only: bool = True,
    featured_only: bool = False,
    search: Optional[str] = None,
) -> ProductListResponse:
    """Get paginated list of products."""
    query = select(Product)
    count_query = select(func.count(Product.id))

    # Apply filters
    if active_only:
        query = query.where(Product.is_active == True)
        count_query = count_query.where(Product.is_active == True)
    
    if category_id:
        query = query.where(Product.category_id == category_id)
        count_query = count_query.where(Product.category_id == category_id)
    
    if featured_only:
        query = query.where(Product.is_featured == True)
        count_query = count_query.where(Product.is_featured == True)
    
    if search:
        search_filter = Product.name.ilike(f"%{search}%")
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    # Count total
    count_result = await db.execute(count_query)
    total = count_result.scalar_one()

    # Get items
    offset = (page - 1) * size
    result = await db.execute(
        query
        .order_by(Product.sort_order, Product.name)
        .offset(offset)
        .limit(size)
    )
    items = list(result.scalars().all())

    # Calculate pages
    pages = (total + size - 1) // size if total > 0 else 1

    return ProductListResponse(
        items=items,
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


async def update_product_sort_order(
    db: AsyncSession,
    product_id: int,
    sort_order: int,
) -> None:
    """Update sort_order for a single product."""
    result = await db.execute(
        select(Product).where(Product.id == product_id)
    )
    product = result.scalar_one_or_none()
    if product:
        product.sort_order = sort_order
        await db.flush()
