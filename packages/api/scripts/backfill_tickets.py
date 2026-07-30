"""CLI: one-off backfill of the one-time lottery tickets (Stage 5, step 2).

Run from ``packages/api`` (uses the api's DATABASE_URL from ``.env``). Always dry-run first —
it executes the real issuance inside a rolled-back SAVEPOINT, so the numbers are the numbers:

    python scripts/backfill_tickets.py --dry-run
    python scripts/backfill_tickets.py --apply  --batch initial-2026-08
    python scripts/backfill_tickets.py --revert --batch initial-2026-08

``--revert`` undoes only what is still undoable: tickets already stamped into a draw stay, because
the published snapshot was taken over those rows. The count of such rows is printed.
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Make ``src`` importable when run directly as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.models_registry  # noqa: F401 — register ALL models on Base.metadata
from src.database import async_session_maker
from src.loyalty.backfill import revert_backfill, run_backfill


async def _dry_run() -> str:
    async with async_session_maker() as session:
        report = await run_backfill(session, dry_run=True)
        return report.render()  # the SAVEPOINT is already rolled back; nothing to commit


async def _apply(batch: str) -> str:
    async with async_session_maker() as session:
        report = await run_backfill(session, batch=batch, dry_run=False)
        await session.commit()
        return report.render()


async def _revert(batch: str) -> str:
    async with async_session_maker() as session:
        report = await revert_backfill(session, batch=batch)
        await session.commit()
        return report.render()


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill one-time lottery tickets.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="count only, write nothing")
    mode.add_argument("--apply", action="store_true", help="issue tickets, marked with --batch")
    mode.add_argument("--revert", action="store_true", help="delete the batch's unplayed tickets")
    parser.add_argument("--batch", default=None, help="batch label, e.g. initial-2026-08")
    args = parser.parse_args()

    if (args.apply or args.revert) and not args.batch:
        # Without a label an --apply run is unrevertable and a --revert run has no target.
        raise SystemExit("--batch is required with --apply and --revert")

    if args.dry_run:
        print(f"DRY RUN (nothing written) · {asyncio.run(_dry_run())}")
    elif args.apply:
        print(f"APPLIED batch={args.batch} · {asyncio.run(_apply(args.batch))}")
    else:
        print(f"REVERTED batch={args.batch} · {asyncio.run(_revert(args.batch))}")


if __name__ == "__main__":
    main()
