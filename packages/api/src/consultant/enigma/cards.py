"""Cards of item products, read from the active catalogue (spec r2 IT-01).

Items are not in the knowledge release, so their cards do not come from its snapshot. The
card is the same public projection the snapshot builds for a supplement.
"""

from collections.abc import Iterable
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.consultant.contracts import Language
from src.consultant.enigma.data import CORES
from src.consultant.knowledge import PUBLIC_FIELDS
from src.products.models import Product
from src.products.router import localize_product

ITEM_PRODUCTS = frozenset(p.shop_id for item in CORES.items for p in item.products)


async def item_cards(
    db: AsyncSession, ids: Iterable[int], language: Language
) -> dict[int, dict[str, Any]]:
    """The card of every requested item product that is active in an active category."""
    wanted = ITEM_PRODUCTS.intersection(ids)
    if not wanted:
        return {}
    products = await db.scalars(
        select(Product).options(joinedload(Product.category)).where(Product.id.in_(wanted))
    )
    cards: dict[int, dict[str, Any]] = {}
    for product in products:
        if not product.is_active or (
            product.category is not None and not product.category.is_active
        ):
            continue
        projection = localize_product(product, language)
        cards[product.id] = jsonable_encoder({key: projection[key] for key in PUBLIC_FIELDS})
    return cards
