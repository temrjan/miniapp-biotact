"""CLI: create, end, re-date and list event promotions (spec event-qr-discount S3, R-04).

Run from ``packages/api`` (uses the api's DATABASE_URL from ``.env``). Dates are Tashkent days:

    python scripts/event_promotions.py create --code nutriforum2026 --title "NUTRIFORUM 2026" \\
        --percent 5 --start 2026-11-28 --end 2026-12-31
    python scripts/event_promotions.py dates --code nutriforum2026 --start 2026-11-28 --end 2027-01-31
    python scripts/event_promotions.py end --code nutriforum2026
    python scripts/event_promotions.py list

A refusal prints the reason, exits with status 1 and writes nothing.
"""

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

# Make ``src`` importable when run directly as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.models_registry  # noqa: F401 — register ALL models on Base.metadata
from src.database import async_session_maker
from src.event_promotions import operator
from src.event_promotions.operator import PromotionCommandError


async def _run(args: argparse.Namespace) -> list[str]:
    async with async_session_maker() as session, session.begin():
        if args.command == "create":
            promotion = await operator.create(
                session,
                code=args.code,
                title=args.title,
                percent=args.percent,
                start=args.start,
                end=args.end,
            )
            return [f"created {operator.describe(promotion)}"]
        if args.command == "end":
            promotion = await operator.end(session, code=args.code)
            return [f"ended {operator.describe(promotion)}"]
        if args.command == "dates":
            before, after = await operator.set_dates(
                session, code=args.code, start=args.start, end=args.end
            )
            return [f"was {before}", f"now {after}"]
        return await operator.listing(session)


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage event promotions.")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create a promotion")
    create.add_argument("--code", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--percent", type=int, required=True)
    create.add_argument("--start", type=date.fromisoformat, required=True, help="YYYY-MM-DD")
    create.add_argument("--end", type=date.fromisoformat, required=True, help="YYYY-MM-DD")
    end = commands.add_parser("end", help="end a running promotion now")
    end.add_argument("--code", required=True)
    dates = commands.add_parser("dates", help="replace the start and end days")
    dates.add_argument("--code", required=True)
    dates.add_argument("--start", type=date.fromisoformat, required=True, help="YYYY-MM-DD")
    dates.add_argument("--end", type=date.fromisoformat, required=True, help="YYYY-MM-DD")
    commands.add_parser("list", help="print every promotion and its window")
    args = parser.parse_args()
    try:
        lines = asyncio.run(_run(args))
    except PromotionCommandError as exc:
        raise SystemExit(f"refused: {exc}") from None
    print("\n".join(lines) if lines else "no promotions")


if __name__ == "__main__":
    main()
