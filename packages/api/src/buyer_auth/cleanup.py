"""Repeatable bounded retention: python -m src.buyer_auth.cleanup (no daemon)."""

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.buyer_auth.models import BuyerAuthLimit, BuyerEmailChallenge, BuyerSession
from src.buyer_auth.security import now
from src.database import async_session_maker


async def cleanup_batch(sessions: async_sessionmaker[AsyncSession], at: datetime) -> int:
    """At most 500 rows total; retain every currently effective limit and authority."""
    removed = 0
    async with sessions.begin() as db:
        await db.execute(text("SET LOCAL lock_timeout='5s'"))
        await db.execute(text("SET LOCAL statement_timeout='30s'"))
        for model, primary, eligible in (
            (
                BuyerEmailChallenge,
                BuyerEmailChallenge.id,
                func.coalesce(BuyerEmailChallenge.terminal_at, BuyerEmailChallenge.expires_at)
                < at - timedelta(hours=24),
            ),
            (
                BuyerSession,
                BuyerSession.id,
                or_(
                    (BuyerSession.revoked_at.is_not(None))
                    & (BuyerSession.revoked_at < at - timedelta(days=7)),
                    func.least(BuyerSession.absolute_expires_at, BuyerSession.idle_expires_at)
                    < at - timedelta(days=7),
                ),
            ),
            (BuyerAuthLimit, BuyerAuthLimit.key, BuyerAuthLimit.expires_at <= at),
        ):
            remaining = 500 - removed
            if remaining == 0:
                break
            keys = (
                select(primary).where(eligible).limit(remaining).with_for_update(skip_locked=True)
            )
            result = await db.scalars(delete(model).where(primary.in_(keys)).returning(primary))
            removed += len(result.all())
    return removed


async def main() -> None:
    from src.database import engine

    try:
        print(await cleanup_batch(async_session_maker, now()))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
