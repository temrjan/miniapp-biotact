"""Loyalty domain services (Stage 2): point balance and ledger reconciliation."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.loyalty.models import PointLedger
from src.loyalty.pricing import compute_cashback


async def get_balance(session: AsyncSession, user_id: int) -> int:
    """Current point balance for a user = ``SUM(point_ledger.amount)`` (0 if none).

    The single source of truth — never a stored column. 1 балл = 1 сум.
    """
    result = await session.execute(
        select(func.coalesce(func.sum(PointLedger.amount), 0)).where(PointLedger.user_id == user_id)
    )
    return int(result.scalar_one())


def earn_row_reconciles(entry: PointLedger) -> bool:
    """Whether an earn row's ``amount`` matches ``compute_cashback(unit_price, rate_bps)``.

    Detects tampering, price/rate drift, or a rounding bug on accrual rows. Rows
    without a provenance snapshot (redeem/expiry/adjust — ``unit_price``/``rate_bps``
    NULL) are not reconcilable this way and return ``True`` (not applicable).
    """
    if entry.unit_price is None or entry.rate_bps is None:
        return True
    return entry.amount == compute_cashback(entry.unit_price, entry.rate_bps)
