"""Cards of item products, read from the active catalogue (spec r2 IT-01).

Items are not in the knowledge release, so their cards do not come from its snapshot. The
card is the same public projection the snapshot builds for a supplement.
"""

from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any, cast

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.categories.models import Category
from src.consultant.contracts import Language
from src.consultant.enigma.data import CORES
from src.consultant.knowledge import PUBLIC_FIELDS
from src.products.models import Product
from src.products.router import localize_product

ITEM_PRODUCTS = frozenset(p.shop_id for item in CORES.items for p in item.products)
SUPPLEMENT_IDS = frozenset(p.shop_id for unit in CORES.units for p in unit.products)


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


async def page_set_card(
    db: AsyncSession, product_id: int, language: Language
) -> dict[str, Any] | None:
    """The localized public card of an active non-supplement, non-item set product."""
    if product_id in SUPPLEMENT_IDS or product_id in ITEM_PRODUCTS:
        return None
    product = await db.scalar(
        select(Product).options(joinedload(Product.category)).where(Product.id == product_id)
    )
    if product is None or not product.is_active:
        return None
    if product.category is not None and not product.category.is_active:
        return None
    projection = localize_product(product, language)
    return cast(dict[str, Any], jsonable_encoder({key: projection[key] for key in PUBLIC_FIELDS}))


async def active_category(db: AsyncSession, category_id: int, language: Language) -> str | None:
    """Localized name of an active catalogue category, or None if missing/inactive."""
    category = await db.scalar(select(Category).where(Category.id == category_id))
    if category is None or not category.is_active:
        return None
    return category.name_uz if language == "uz" and category.name_uz else category.name


def format_price(card: Mapping[str, Any]) -> str:
    """Server-formatted live selling price without sale/discount labels (UX-P03)."""
    raw = (
        card["sale_price"]
        if card.get("is_sale") and card.get("sale_price") is not None
        else card["price"]
    )
    currency = str(card.get("currency") or "UZS")
    amount = raw if isinstance(raw, Decimal) else Decimal(str(raw))
    if amount == amount.to_integral_value():
        whole = int(amount)
        return f"{whole:,}".replace(",", " ") + f" {currency} ({whole})"
    norm_str = format(amount.normalize(), "f")
    whole_str, _, frac_str = norm_str.partition(".")
    spaced_whole = f"{int(whole_str):,}".replace(",", " ")
    return f"{spaced_whole}.{frac_str} {currency} ({norm_str})"
