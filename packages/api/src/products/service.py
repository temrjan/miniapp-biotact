"""Product service layer."""

from dataclasses import dataclass
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.products.models import Product
from src.products.schemas import ProductCreate, ProductUpdate
from src.products.stock import lock_products


class SaleInvariantError(Exception):
    """The merged product violates the sale price invariant."""


def _validate_sale(price: Decimal | None, is_sale: bool, sale_price: Decimal | None) -> None:
    if (
        price is None
        or price <= 0
        or (is_sale and (sale_price is None or not 0 < sale_price < price))
    ):
        raise SaleInvariantError


def _constraint_name(exc: IntegrityError) -> str | None:
    current: BaseException | None = exc.orig
    while current is not None:
        name = getattr(current, "constraint_name", None)
        if isinstance(name, str) and name:
            return name
        current = current.__cause__
    return None


async def _flush_product(db: AsyncSession) -> None:
    try:
        await db.flush()
    except IntegrityError as exc:
        if _constraint_name(exc) != "products_sale_ck":
            raise
        await db.rollback()
        raise SaleInvariantError from exc


@dataclass
class PaginatedResult:
    """Internal paginated result container with SQLAlchemy models."""

    items: list[Product]
    total: int
    page: int
    size: int
    pages: int


async def get_product_by_id(
    db: AsyncSession,
    product_id: int,
) -> Product | None:
    """Get product by ID."""
    result = await db.execute(
        select(Product).options(joinedload(Product.category)).where(Product.id == product_id)
    )
    return result.scalar_one_or_none()


async def get_product_by_slug(
    db: AsyncSession,
    slug: str,
) -> Product | None:
    """Get product by slug."""
    result = await db.execute(
        select(Product).options(joinedload(Product.category)).where(Product.slug == slug)
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
    _validate_sale(product_data.price, product_data.is_sale, product_data.sale_price)
    data = product_data.model_dump()
    # JSONB automatically handles Python lists - no json.dumps needed!
    # SQLAlchemy will convert List[str] -> JSONB automatically

    # Auto-set image_url from first image if not provided
    if not data.get("image_url") and data.get("images") and len(data["images"]) > 0:
        data["image_url"] = data["images"][0]

    product = Product(**data)
    db.add(product)
    await _flush_product(db)
    await db.refresh(product)
    loaded = await get_product_by_id(db, product.id)
    assert loaded is not None
    return loaded


async def update_product(
    db: AsyncSession,
    product_id: int,
    update_data: ProductUpdate,
) -> Product | None:
    """Update product.

    JSONB fields (images, advantages) are automatically converted by SQLAlchemy.
    No need for json.dumps anymore!
    """
    product = (await lock_products(db, {product_id})).get(product_id)
    if not product:
        return None

    update_dict = update_data.model_dump(exclude_unset=True)
    if product.requires_size and "stock_quantity" in update_dict:
        raise HTTPException(
            status_code=422,
            detail={"code": "legacy_stock_not_editable", "product_id": product_id, "size": None},
        )
    _validate_sale(
        update_dict.get("price", product.price),
        update_dict.get("is_sale", product.is_sale),
        update_dict.get("sale_price", product.sale_price),
    )
    # JSONB automatically handles Python lists - no json.dumps needed!
    # SQLAlchemy will convert List[str] -> JSONB automatically

    # Auto-set image_url from first image if not provided
    if (
        "images" in update_dict
        and update_dict["images"]
        and len(update_dict["images"]) > 0
        and not update_dict.get("image_url")
        and not product.image_url
    ):
        update_dict["image_url"] = update_dict["images"][0]

    for field, value in update_dict.items():
        setattr(product, field, value)

    await _flush_product(db)
    await db.refresh(product)
    return product


async def delete_product(
    db: AsyncSession,
    product_id: int,
) -> bool:
    """Delete product."""
    product = (await lock_products(db, {product_id})).get(product_id)
    if not product:
        return False

    if product.requires_size or product.sizes_initialized_at is not None:
        raise HTTPException(status_code=409, detail={"code": "size_configuration_exists"})

    await db.delete(product)
    return True


async def get_products_paginated(
    db: AsyncSession,
    page: int = 1,
    size: int = 20,
    category_id: int | None = None,
    active_only: bool = True,
    featured_only: bool = False,
    on_sale: bool = False,
    search: str | None = None,
    sort_by: str | None = None,
) -> PaginatedResult:
    """Get paginated list of products."""
    query = select(Product).options(joinedload(Product.category))
    count_query = select(func.count(Product.id))

    # Apply filters
    if active_only or on_sale:
        query = query.where(Product.is_active.is_(True))
        count_query = count_query.where(Product.is_active.is_(True))

    if on_sale:
        query = query.where(Product.is_sale.is_(True))
        count_query = count_query.where(Product.is_sale.is_(True))

    if category_id:
        query = query.where(Product.category_id == category_id)
        count_query = count_query.where(Product.category_id == category_id)

    if featured_only:
        query = query.where(Product.is_featured.is_(True))
        count_query = count_query.where(Product.is_featured.is_(True))

    if search:
        search_filter = Product.name.ilike(f"%{search}%")
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)

    # Count total
    count_result = await db.execute(count_query)
    total = count_result.scalar_one()

    # Get items
    offset = (page - 1) * size

    # Apply sorting
    if sort_by == "id_desc":
        query = query.order_by(Product.id.desc())
    elif sort_by == "id_asc":
        query = query.order_by(Product.id.asc())
    elif sort_by == "name_asc":
        query = query.order_by(Product.name.asc())
    elif sort_by == "name_desc":
        query = query.order_by(Product.name.desc())
    elif sort_by == "price_asc":
        query = query.order_by(Product.price.asc())
    elif sort_by == "price_desc":
        query = query.order_by(Product.price.desc())
    else:
        query = query.order_by(Product.sort_order, Product.name)

    result = await db.execute(query.offset(offset).limit(size))
    items = list(result.unique().scalars().all())

    # Calculate pages
    pages = (total + size - 1) // size if total > 0 else 1

    return PaginatedResult(
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
    result = await db.execute(select(Product).where(Product.id == product_id))
    product = result.scalar_one_or_none()
    if product:
        product.sort_order = sort_order
        await db.flush()
