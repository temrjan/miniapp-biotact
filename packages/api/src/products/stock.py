"""Size balances: sorted row locks, private edits, allocation and restoration."""

from typing import Annotated

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.orders.models import Order
from src.products.models import Product, ProductSize

SIZES = ("L", "XL", "2XL")
Quantity = Annotated[StrictInt, Field(ge=0, le=2147483647)]
Revision = Annotated[StrictInt, Field(ge=0)]


class SizeQuantity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    size: str
    available_quantity: Quantity
    expected_revision: Revision


class SizeQuantitiesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sizes: list[SizeQuantity]


class SizeQuantityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    size: str
    available_quantity: int
    revision: int


class SizeInventoryResponse(BaseModel):
    product_id: int
    merchant_is_in_stock: bool
    sizes: list[SizeQuantityResponse]


def size_error(code: str, product_id: int, size: str | None, status: int = 422) -> HTTPException:
    return HTTPException(
        status_code=status, detail={"code": code, "product_id": product_id, "size": size}
    )


async def lock_products(db: AsyncSession, product_ids: set[int]) -> dict[int, Product]:
    rows = (
        (
            await db.execute(
                select(Product)
                .options(joinedload(Product.category))
                .where(Product.id.in_(product_ids))
                .order_by(Product.id)
                .with_for_update(of=Product)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    return {row.id: row for row in rows}


async def lock_sizes(db: AsyncSession, product_ids: set[int]) -> dict[int, ProductSize]:
    rows = (
        (
            await db.execute(
                select(ProductSize)
                .where(ProductSize.product_id.in_(product_ids))
                .order_by(ProductSize.product_id, ProductSize.size)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    return {row.id: row for row in rows}


def inventory_response(product: Product, rows: list[ProductSize]) -> SizeInventoryResponse:
    by_label = {row.size: row for row in rows}
    return SizeInventoryResponse(
        product_id=product.id,
        merchant_is_in_stock=product.is_in_stock,
        sizes=[
            SizeQuantityResponse.model_validate(by_label[label])
            for label in SIZES
            if label in by_label
        ],
    )


async def read_inventory(db: AsyncSession, product_id: int) -> SizeInventoryResponse:
    product = await db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return inventory_response(product, product.sizes)


async def update_inventory(
    db: AsyncSession, product_id: int, update: SizeQuantitiesUpdate
) -> SizeInventoryResponse:
    products = await lock_products(db, {product_id})
    if product_id not in products:
        raise HTTPException(status_code=404, detail="Product not found")
    rows = list((await lock_sizes(db, {product_id})).values())
    if not rows:
        raise HTTPException(status_code=409, detail={"code": "size_configuration_missing"})
    submitted = {entry.size: entry for entry in update.sizes}
    if len(submitted) != len(update.sizes) or set(submitted) != {row.size for row in rows}:
        raise HTTPException(status_code=422, detail="Supply each configured size exactly once")
    if any(row.revision != submitted[row.size].expected_revision for row in rows):
        raise HTTPException(status_code=409, detail={"code": "size_stock_conflict"})
    for row in rows:
        row.available_quantity = submitted[row.size].available_quantity
        row.revision += 1
    await db.flush()
    return inventory_response(products[product_id], rows)


async def restore_allocations(
    db: AsyncSession, order: Order, quantities: dict[int, int] | None = None
) -> None:
    """Caller owns the order lock; acquire product/size locks before other accounting writes."""
    lines = [item for item in order.items if item.size_id is not None]
    product_ids = {item.product_id for item in lines if item.product_id is not None}
    await lock_products(db, product_ids)
    rows = await lock_sizes(db, product_ids)
    for item in lines:
        amount = (
            item.allocated_quantity - item.restored_quantity
            if quantities is None
            else quantities.get(item.id, 0)
        )
        if amount < 0 or amount > item.allocated_quantity - item.restored_quantity:
            raise HTTPException(status_code=422, detail="Return exceeds outstanding allocation")
        if amount and item.size_id is not None:
            row = rows[item.size_id]
            if row.available_quantity + amount > 2147483647:
                raise HTTPException(status_code=409, detail={"code": "size_stock_conflict"})
            row.available_quantity += amount
            row.revision += 1
            item.restored_quantity += amount
