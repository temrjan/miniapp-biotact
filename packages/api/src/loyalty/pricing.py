"""Loyalty cashback price catalog and money math (Stage 2).

Single source of the cashback base price per SKU. This is **not** reference data —
``unit_price`` directly computes a financial liability (the points a customer earns),
so it lives versioned in code: changing a price is a reviewed, deployed change, never
a silent admin toggle.

Units (invariant): ``unit_price`` is an integer in **UZS including VAT**. Points are
whole integers, **1 балл = 1 сум**. No floats anywhere — cashback is computed with
exact ``Decimal`` arithmetic and ``ROUND_HALF_UP``.
"""

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, ConfigDict, Field


class UnknownSkuError(Exception):
    """A SKU has no cashback price configured.

    Surfaced loudly rather than silently accruing zero (or a wrong default): a QR
    whose SKU is not in the catalog is a real operational event (a product not yet
    entered), to be handled explicitly — never swallowed to 0 or 500.
    """

    def __init__(self, sku: str) -> None:
        self.sku = sku
        super().__init__(f"no cashback unit_price configured for SKU {sku!r}")


class SkuPrice(BaseModel):
    """Immutable cashback base price for one SKU (integer UZS incl. VAT)."""

    model_config = ConfigDict(frozen=True)

    sku: str
    unit_price: int = Field(gt=0)  # integer UZS, VAT included
    currency: str = "UZS"
    vat_included: bool = True


# ---------------------------------------------------------------------------
# PLACEHOLDER CATALOG.
# The real 13 SKU prices (UZS incl. VAT) land in PR-2.3, together with a guard
# test that fails if any sentinel survives. Until then the loyalty endpoint is
# dark (loyalty_enabled=false) so nothing reads a real price. The sentinel below
# keeps the module valid/importable and the math testable.
# ---------------------------------------------------------------------------
SENTINEL_SKU = "__PLACEHOLDER_SKU__"
SENTINEL_UNIT_PRICE = 1

_CATALOG: dict[str, SkuPrice] = {
    SENTINEL_SKU: SkuPrice(sku=SENTINEL_SKU, unit_price=SENTINEL_UNIT_PRICE),
}


def validate_catalog() -> None:
    """Fail-fast on a malformed catalog (non-empty; prices are positive integers).

    Called at import time so a broken catalog stops the app at startup rather than
    at the first activation. ``SkuPrice`` already enforces ``unit_price > 0`` per
    entry; this guards the catalog as a whole.
    """
    if not _CATALOG:
        raise RuntimeError("loyalty price catalog is empty")
    for sku, price in _CATALOG.items():
        if price.sku != sku:
            raise RuntimeError(f"catalog key {sku!r} != entry.sku {price.sku!r}")


def get_unit_price(sku: str) -> int:
    """Cashback base price (integer UZS incl. VAT) for ``sku``.

    Raises :class:`UnknownSkuError` if the SKU is not priced — never returns 0.
    """
    try:
        return _CATALOG[sku].unit_price
    except KeyError:
        raise UnknownSkuError(sku) from None


def compute_cashback(unit_price: int, rate_bps: int) -> int:
    """Cashback points for ``unit_price`` at ``rate_bps`` basis points.

    ``rate_bps`` = basis points (500 → 5%, 800 → 8%, 250 → welcome half-rate).
    Exact ``Decimal`` math, ``ROUND_HALF_UP`` (a .5 tie rounds up — in the
    customer's favour on earn). Returns whole points (1 балл = 1 сум).
    """
    raw = Decimal(unit_price) * Decimal(rate_bps) / Decimal(10_000)
    return int(raw.quantize(Decimal(1), rounding=ROUND_HALF_UP))


validate_catalog()
