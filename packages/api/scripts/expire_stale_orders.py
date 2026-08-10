"""CLI: reclaim abandoned orders that no one else cancels — the periodic sweep.

Run from ``packages/api`` (uses the api's DATABASE_URL from ``.env``):

    python scripts/expire_stale_orders.py --dry-run
    python scripts/expire_stale_orders.py --apply
    python scripts/expire_stale_orders.py --apply --max-age-minutes 30

Meant for cron. Frequency is a CORRECTNESS constraint — see the comment on
``expire_stale_orders``: a Multicard invoice lives ~24h and cancelling requires deleting it first,
so a daily run leaves orders permanently uncancellable. Hours, not days.

``--dry-run`` touches NOTHING: no database write AND no Multicard call. That is deliberate — a
"dry run" that still annulled real invoices burned the author once (2026-08-07 cleanup).
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Раньше эта строка стояла первой среди src.* не ради моделей, а ради порядка: в пакетах жил цикл
# src.users.router <-> src.auth.dependencies, из-за которого этот скрипт умирал на первом же
# импорте прямо на проде. Цикл снят (src/users/__init__.py), и подпорка больше не нужна — но
# импорт оставлен: он регистрирует модели на Base.metadata, что для скрипта, ходящего по cron,
# дешевле, чем зависеть от того, какие модули подтянет цепочка импортов сегодня.
import src.models_registry  # noqa: F401
from src.config import settings
from src.orders.models import Order, OrderStatus
from src.orders.service import REDEEM_RESERVE_TIMEOUT, expire_stale_orders


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="only count; no DB, no Multicard")
    mode.add_argument("--apply", action="store_true", help="delete invoices and cancel orders")
    parser.add_argument(
        "--max-age-minutes",
        type=int,
        default=int(REDEEM_RESERVE_TIMEOUT.total_seconds() // 60),
        help="how long an order may sit unpaid before it is reclaimed",
    )
    return parser.parse_args()


async def main() -> int:
    args = _parse_args()
    max_age = timedelta(minutes=args.max_age_minutes)
    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        if args.dry_run:
            # Считаем ТЕМ ЖЕ условием, что и уборка, но не трогаем ни базу, ни Multicard.
            async with session_factory() as session:
                rows = (
                    (
                        await session.execute(
                            select(Order).where(
                                Order.status == OrderStatus.PENDING,
                                Order.payment_status.in_(("unpaid", "pending")),
                                Order.created_at < datetime.now(UTC) - max_age,
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            print(f"dry-run: {len(rows)} order(s) older than {max_age} would be reclaimed")
            for order in rows:
                print(f"  #{order.id} {order.order_number} points={order.points_applied}")
            return 0

        async with session_factory() as session:
            outcome = await expire_stale_orders(session, max_age=max_age)
            await session.commit()
        print(
            f"reclaimed: {outcome.released_orders} order(s), "
            f"{outcome.released_points} point(s) released, "
            f"{outcome.blocked_orders} left alone (invoice refused deletion)"
        )
        return 0
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
