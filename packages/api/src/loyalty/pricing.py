"""Loyalty cashback pricing and money math (Stage 2).

The cashback base is the product's own price (``products.price``), looked up by
``product_id`` — always current, single source of truth. Each accrual snapshots the
applied ``unit_price``/``rate_bps`` onto the ledger (Q4), so a later price change only
affects future accruals; past liability is immutable.

Units (invariant): ``unit_price`` is an integer in **UZS including VAT** (``products.price``
is the gross amount Multicard charges). Points are whole integers, **1 балл = 1 сум**.
No floats — cashback is computed with exact ``Decimal`` arithmetic and ``ROUND_HALF_UP``.
"""

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.products.models import Product


class PricingError(Exception):
    """A product cannot be priced for cashback right now — surfaced loudly, never accrued as 0.

    Either the product is missing/unpriced (:class:`UnknownProductError`) or its price is not
    whole UZS (:class:`FractionalPriceError`). Both are real operational events: the activation
    rolls the claim back and the caller maps them to a retryable error — money is never silently
    wrong. Carries ``product_id`` for the ops log.
    """

    def __init__(self, product_id: int, detail: str) -> None:
        self.product_id = product_id
        super().__init__(f"{detail} for product_id={product_id}")


class UnknownProductError(PricingError):
    """The product is missing, or its ``price`` is NULL (never silently accrue 0)."""

    def __init__(self, product_id: int) -> None:
        super().__init__(product_id, "no cashback price")


class FractionalPriceError(PricingError, ValueError):
    """The product's ``price`` has a fractional part — whole UZS expected (no silent truncation).

    Also a :class:`ValueError` so the PR-2.1 pricing tests keep matching without change.
    """

    def __init__(self, product_id: int, price: Decimal) -> None:
        super().__init__(product_id, f"fractional price {price}")


async def get_unit_price(session: AsyncSession, product_id: int) -> int:
    """Cashback base price (integer UZS incl. VAT) for ``product_id`` = ``products.price``.

    Looked up by primary key (unique, indexed, deterministic). Raises a :class:`PricingError`
    — :class:`UnknownProductError` if the product is missing or its ``price`` is NULL,
    :class:`FractionalPriceError` if the price is not whole UZS — never returns 0. Cashback is
    priced regardless of ``is_active`` (a purchased box still earns even if later delisted).
    """
    price = (
        await session.execute(select(Product.price).where(Product.id == product_id))
    ).scalar_one_or_none()
    if price is None:
        raise UnknownProductError(product_id)
    # Cashback base must be whole UZS — guard against a silent truncation if a fractional
    # price ever lands in the column (the schema allows Numeric(12,2)).
    if price != price.to_integral_value():
        raise FractionalPriceError(product_id, price)
    return int(price)


def compute_cashback(unit_price: int, rate_bps: int) -> int:
    """Cashback points for ``unit_price`` at ``rate_bps`` basis points.

    ``rate_bps`` = basis points (500 → 5%, 800 → 8%, 250 → welcome half-rate).
    Exact ``Decimal`` math, ``ROUND_HALF_UP`` (a .5 tie rounds up — in the customer's
    favour on earn). Returns whole points (1 балл = 1 сум).
    """
    raw = Decimal(unit_price) * Decimal(rate_bps) / Decimal(10_000)
    return int(raw.quantize(Decimal(1), rounding=ROUND_HALF_UP))
