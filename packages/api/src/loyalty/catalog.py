"""13-SKU price catalog — prices with VAT, May 2026 (strategy §3.2 / §8.6).

The cashback basis for a code batch defaults from this catalog. ``sku_code`` is
the internal index embedded in the QR (``BTQR-2026-<sku_code>-NNNNNNN``); only
``ZNK`` is fixed by the strategy doc, the rest are proposed and can be renamed
here before printing (we generate the codes, so the scheme is ours).
"""

from decimal import Decimal
from typing import NamedTuple


class SkuPrice(NamedTuple):
    """A SKU's internal code, display name and list price (UZS, incl. VAT)."""

    code: str
    name: str
    price: Decimal


# Keyed by sku_code. Order follows the portfolio table (§3.2).
SKU_CATALOG: dict[str, SkuPrice] = {
    "ZNK": SkuPrice("ZNK", "BIFOLAK-ZINCUM стик", Decimal("75500")),
    "NEO": SkuPrice("NEO", "BIFOLAK-NEO стик", Decimal("66500")),
    "ACT": SkuPrice("ACT", "BIFOLAK-ACTIVE стик", Decimal("75500")),
    "MAG": SkuPrice("MAG", "BIFOLAK-MAGNIY стик", Decimal("83500")),
    "IMK": SkuPrice("IMK", "IMMUNOCOMPLEX KIDS стик", Decimal("76000")),
    "ACC": SkuPrice("ACC", "BIFOLAK-ACTIVE капс.", Decimal("75500")),
    "ZCD": SkuPrice("ZCD", "BIFOLAK-ZINCUM+C+D3 капс.", Decimal("76000")),
    "CAL": SkuPrice("CAL", "CALCIY TRIACTIVE D3 стик", Decimal("76000")),
    "IMM": SkuPrice("IMM", "IMMUNOCOMPLEX капс.", Decimal("76000")),
    "MGC": SkuPrice("MGC", "BIFOLAK-MAGNIY капс.", Decimal("83500")),
    "NRK": SkuPrice("NRK", "NEUROCOMPLEX KIDS стик", Decimal("101000")),
    "DRM": SkuPrice("DRM", "DERMACOMPLEX капс.", Decimal("94000")),
    "OPH": SkuPrice("OPH", "OPHTALMOCOMPLEX капс.", Decimal("123000")),
}
