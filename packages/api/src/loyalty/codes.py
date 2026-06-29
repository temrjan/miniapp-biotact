"""QR code batch generation and CSV export (Stage 2).

Codes are opaque random tokens (Crockford base32, ~60 bits) — unguessable enough that
the "no arming" model is safe, short enough for the Telegram deeplink ``qr_<code>`` (well
under the 64-char limit). The product a code carries is the ``product_id`` FK, never
encoded in the string.
"""

import csv
import io
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.loyalty.models import CodeBatch, QrCode
from src.loyalty.pricing import get_unit_price

# Crockford base32 — no I/L/O/U (avoids visual ambiguity on printed boxes).
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 12  # 12 * 5 bits = 60 bits of entropy


def generate_code() -> str:
    """A single opaque code: ``CODE_LENGTH`` Crockford base32 chars (~60 bits)."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))


async def _generate_unique_codes(session: AsyncSession, quantity: int) -> list[str]:
    """Generate ``quantity`` codes unique within the batch and against existing rows.

    Deduplicates in memory and pre-checks the DB so a collision never reaches the
    insert. ``UNIQUE(qr_codes.code)`` remains the ultimate guard (e.g. a concurrent
    generator). At 60 bits the first pass almost always yields all-unique codes.
    """
    codes: set[str] = set()
    while len(codes) < quantity:
        candidates = {generate_code() for _ in range(quantity - len(codes))} - codes
        existing = set(
            (await session.execute(select(QrCode.code).where(QrCode.code.in_(candidates))))
            .scalars()
            .all()
        )
        codes |= candidates - existing
    return list(codes)


async def generate_batch(
    session: AsyncSession,
    product_id: int,
    quantity: int,
    *,
    label: str | None = None,
) -> tuple[CodeBatch, list[QrCode]]:
    """Create a :class:`CodeBatch` of ``quantity`` unique codes for ``product_id``.

    Fail-fast: the product must be priced (raises
    :class:`~src.loyalty.pricing.UnknownProductError`) — never print a QR for a product that
    cannot accrue cashback. The ``product_id`` FK also rejects a non-existent product.
    The caller owns the transaction (commits).
    """
    if quantity < 1:
        raise ValueError("quantity must be >= 1")
    await get_unit_price(session, product_id)  # fail-fast: product exists and is priced

    batch = CodeBatch(product_id=product_id, quantity=quantity, label=label)
    session.add(batch)
    await session.flush()  # assign batch.id

    codes = await _generate_unique_codes(session, quantity)
    qr_codes = [QrCode(code=code, product_id=product_id, batch_id=batch.id) for code in codes]
    session.add_all(qr_codes)
    await session.flush()  # UNIQUE(code) enforced here
    return batch, qr_codes


def codes_to_csv(qr_codes: list[QrCode]) -> str:
    """Render codes as CSV (``code,product_id``) for the printing house."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["code", "product_id"])
    for qr in qr_codes:
        writer.writerow([qr.code, qr.product_id])
    return buffer.getvalue()
