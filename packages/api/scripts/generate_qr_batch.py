"""CLI: generate a batch of QR codes for a product and export them to CSV.

Run from ``packages/api`` (uses the api's DATABASE_URL from ``.env``):

    python scripts/generate_qr_batch.py --slug bifolak-neo --quantity 1000 --out batch.csv
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Make ``src`` importable when run directly as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

import src.models_registry  # noqa: F401 — register ALL models on Base.metadata (resolves Consent→User etc.)
from src.database import async_session_maker
from src.loyalty.codes import codes_to_csv, generate_batch
from src.products.models import Product


async def _generate(slug: str, quantity: int, label: str | None) -> tuple[int, str, int]:
    """Resolve slug -> product_id, generate+persist a batch; return (batch_id, csv, count)."""
    async with async_session_maker() as session:
        product_id = (
            await session.execute(select(Product.id).where(Product.slug == slug))
        ).scalar_one_or_none()
        if product_id is None:
            raise SystemExit(f"no product with slug {slug!r}")
        batch, qr_codes = await generate_batch(session, product_id, quantity, label=label)
        await session.commit()
        return batch.id, codes_to_csv(qr_codes), len(qr_codes)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a QR code batch and export to CSV.")
    parser.add_argument("--slug", required=True, help="product slug (must be priced)")
    parser.add_argument("--quantity", type=int, required=True, help="number of codes to generate")
    parser.add_argument("--label", default=None, help="optional batch label")
    parser.add_argument("--out", type=Path, required=True, help="CSV output path")
    args = parser.parse_args()

    batch_id, csv_text, count = asyncio.run(_generate(args.slug, args.quantity, args.label))
    args.out.write_text(csv_text)
    print(f"batch {batch_id}: {count} codes for {args.slug} -> {args.out}")


if __name__ == "__main__":
    main()
