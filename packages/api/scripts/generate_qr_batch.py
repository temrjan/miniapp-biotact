"""CLI: generate a batch of QR codes for a SKU and export them to CSV.

Run from ``packages/api`` (uses the api's DATABASE_URL from ``.env``):

    python scripts/generate_qr_batch.py --sku BIFOLAK-STICK --quantity 1000 --out batch.csv
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Make ``src`` importable when run directly as a script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database import async_session_maker
from src.loyalty.codes import codes_to_csv, generate_batch


async def _generate(sku: str, quantity: int, label: str | None) -> tuple[int, str, int]:
    """Generate+persist a batch; return (batch_id, csv_text, count). No file I/O here."""
    async with async_session_maker() as session:
        batch, qr_codes = await generate_batch(session, sku, quantity, label=label)
        await session.commit()
        return batch.id, codes_to_csv(qr_codes), len(qr_codes)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a QR code batch and export to CSV.")
    parser.add_argument("--sku", required=True, help="SKU (must be priced in pricing.py)")
    parser.add_argument("--quantity", type=int, required=True, help="number of codes to generate")
    parser.add_argument("--label", default=None, help="optional batch label")
    parser.add_argument("--out", type=Path, required=True, help="CSV output path")
    args = parser.parse_args()

    batch_id, csv_text, count = asyncio.run(_generate(args.sku, args.quantity, args.label))
    args.out.write_text(csv_text)
    print(f"batch {batch_id}: {count} codes for {args.sku} -> {args.out}")


if __name__ == "__main__":
    main()
