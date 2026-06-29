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


class UnknownProductError(Exception):
    """A product has no usable cashback price (not found, or ``price`` is NULL).

    Surfaced loudly rather than silently accruing zero (or a wrong default): a QR whose
    product is missing or unpriced is a real operational event, to be handled explicitly
    (the activation rolls back the claim) — never swallowed to 0.
    """

    def __init__(self, product_id: int) -> None:
        self.product_id = product_id
        super().__init__(f"no cashback price for product_id={product_id}")


async def get_unit_price(session: AsyncSession, product_id: int) -> int:
    """Cashback base price (integer UZS incl. VAT) for ``product_id`` = ``products.price``.

    Looked up by primary key (unique, indexed, deterministic). Raises
    :class:`UnknownProductError` if the product is missing or its ``price`` is NULL — never
    returns 0. Cashback is priced regardless of ``is_active`` (a purchased box still earns
    even if the product was later delisted from the shop).
    """
    price = (
        await session.execute(select(Product.price).where(Product.id == product_id))
    ).scalar_one_or_none()
    if price is None:
        raise UnknownProductError(product_id)
    # Cashback base must be whole UZS — guard against a silent truncation if a fractional
    # price ever lands in the column (the schema allows Numeric(12,2)).
    if price != price.to_integral_value():
        raise ValueError(
            f"product_id={product_id} has a fractional price {price}; expected whole UZS"
        )
    return int(price)


def compute_cashback(unit_price: int, rate_bps: int) -> int:
    """Cashback points for ``unit_price`` at ``rate_bps`` basis points.

    ``rate_bps`` = basis points (500 → 5%, 800 → 8%, 250 → welcome half-rate).
    Exact ``Decimal`` math, ``ROUND_HALF_UP`` (a .5 tie rounds up — in the customer's
    favour on earn). Returns whole points (1 балл = 1 сум).
    """
    raw = Decimal(unit_price) * Decimal(rate_bps) / Decimal(10_000)
    return int(raw.quantize(Decimal(1), rounding=ROUND_HALF_UP))
