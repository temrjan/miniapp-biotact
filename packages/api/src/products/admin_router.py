"""Protected, revision-checked quantities; buyer product schemas expose no counts."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import require_admin, verify_admin_jwt
from src.database import get_db
from src.products.stock import (
    SizeInventoryResponse,
    SizeQuantitiesUpdate,
    read_inventory,
    update_inventory,
)

router = APIRouter()
DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("/{product_id}/sizes", dependencies=[Depends(verify_admin_jwt)])
async def get_sizes(product_id: int, db: DbSession) -> SizeInventoryResponse:
    return await read_inventory(db, product_id)


@router.patch("/{product_id}/sizes", dependencies=[Depends(require_admin)])
async def save_sizes(
    product_id: int, data: SizeQuantitiesUpdate, db: DbSession
) -> SizeInventoryResponse:
    return await update_inventory(db, product_id, data)
