"""Multicard payment service."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from src.config import settings
from src.payments.schemas import (
    MultiCardPaymentResponse,
    OFDItem,
    PaymentCreateData,
)

if TYPE_CHECKING:
    from src.orders.models import OrderItem

logger = logging.getLogger(__name__)


class MultiCardClient:
    """Multicard API client."""

    def __init__(self) -> None:
        self.base_url = settings.multicard_base_url
        self.application_id = settings.multicard_application_id
        self.secret = settings.multicard_secret.get_secret_value()
        self.store_id = settings.multicard_store_id
        self._token: str | None = None
        self._token_expiry: datetime | None = None

    async def _get_token(self) -> str:
        """Get or refresh auth token."""
        # Check if we have a valid cached token
        if self._token and self._token_expiry and datetime.now() < self._token_expiry:
            return self._token

        url = f"{self.base_url}/auth"
        payload = {
            "application_id": self.application_id,
            "secret": self.secret,
        }

        logger.info(f"Getting auth token from {url}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                data = response.json()

                self._token = data["token"]
                # Token valid for 24h, refresh 1h before expiry
                self._token_expiry = datetime.now() + timedelta(hours=23)

                logger.info("Auth token obtained successfully")
                return self._token

            except httpx.HTTPStatusError as e:
                logger.error(f"Auth error: {e.response.status_code} - {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Failed to get auth token: {e}")
                raise

    def _get_headers(self, token: str) -> dict[str, str]:
        """Get authorization headers."""
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def verify_callback_sign(
        self,
        store_id: int,
        invoice_id: str,
        amount: int,
        sign: str,
    ) -> bool:
        """Verify success callback signature (MD5)."""
        import hmac as _hmac

        # MD5: {store_id}{invoice_id}{amount}{secret}
        data = f"{store_id}{invoice_id}{amount}{self.secret}"
        expected_sign = hashlib.md5(data.encode()).hexdigest()
        return _hmac.compare_digest(sign, expected_sign)

    def verify_webhook_sign(
        self,
        uuid: str,
        invoice_id: str,
        amount: int,
        sign: str,
    ) -> bool:
        """Verify webhook signature (SHA1)."""
        import hmac as _hmac

        # SHA1: {uuid}{invoice_id}{amount}{secret}
        data = f"{uuid}{invoice_id}{amount}{self.secret}"
        expected_sign = hashlib.sha1(data.encode()).hexdigest()
        return _hmac.compare_digest(sign, expected_sign)

    async def create_invoice(
        self,
        payment_data: PaymentCreateData,
    ) -> MultiCardPaymentResponse | None:
        """Create payment invoice in Multicard."""
        token = await self._get_token()
        url = f"{self.base_url}/payment/invoice"

        payload = {
            "store_id": self.store_id,
            "amount": payment_data.amount,
            "invoice_id": payment_data.invoice_id,
            "callback_url": payment_data.callback_url,
            "return_url": payment_data.return_url,
            "lang": payment_data.lang,
            "ofd": [item.model_dump(exclude_none=True) for item in payment_data.ofd],
        }

        logger.info(
            f"Creating invoice: invoice_id={payment_data.invoice_id}, amount={payment_data.amount}"
        )

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    url,
                    json=payload,
                    headers=self._get_headers(token),
                )
                response.raise_for_status()
                result = response.json()

                if not result.get("success"):
                    error = result.get("error", {})
                    logger.error(f"Multicard error: {error}")
                    raise Exception(f"Multicard error: {error.get('details', 'Unknown error')}")

                data = result["data"]
                logger.info(f"Invoice created: uuid={data.get('uuid')}")

                return MultiCardPaymentResponse(
                    uuid=data["uuid"],
                    pay_url=data.get("checkout_url") or data.get("short_link"),
                )

            except httpx.HTTPStatusError as e:
                logger.error(f"Multicard API error: {e.response.status_code} - {e.response.text}")
                raise
            except Exception as e:
                logger.error(f"Failed to create invoice: {e}")
                raise

    async def get_invoice_status(self, uuid: str) -> dict[str, Any] | None:
        """Get invoice status from Multicard."""
        token = await self._get_token()
        url = f"{self.base_url}/payment/invoice/{uuid}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(url, headers=self._get_headers(token))
                response.raise_for_status()
                result = response.json()
                if result.get("success"):
                    data: dict[str, Any] | None = result.get("data")
                    return data
                return None
            except Exception as e:
                logger.error(f"Failed to get invoice status: {e}")
                return None


# Singleton instance
multicard_client = MultiCardClient()


def _scale_line_totals(full_totals: list[int], *, amount_tiyins: int, sum_full: int) -> list[int]:
    """Spread ``amount_tiyins`` across lines pro-rata by full total; the last line absorbs residue.

    Each line floors to ``full_i * amount_tiyins // sum_full`` (so the running sum never overshoots),
    then the final line takes the leftover tiyins — making ``sum(result) == amount_tiyins`` exact.
    """
    scaled = [full * amount_tiyins // sum_full for full in full_totals]
    scaled[-1] += amount_tiyins - sum(scaled)
    return scaled


def build_ofd_items(order_items: list[OrderItem], *, amount_tiyins: int) -> list[OFDItem]:
    """Build OFD line items whose totals sum EXACTLY to ``amount_tiyins`` (the discount model).

    A card order (or a fully points-covered one, which never reaches Multicard) is charged the full
    catalogue sum, so ``amount_tiyins`` equals the untouched line totals and every line passes
    through verbatim — byte-for-byte the pre-Stage-6 behaviour. A partially points-covered order is
    charged only the card remainder: the points discount is spread across lines pro-rata by line
    total, and the LAST line absorbs the rounding residue so ``sum(line.total) == amount_tiyins`` to
    the tiyin.

    The receipt is filed with the tax authority, so a fiscal document disagreeing with the sum
    actually charged is a defect: the invariant is enforced with a ``raise`` (it survives
    ``python -O``), never a strippable ``assert``.

    Args:
        order_items: The order's lines — each carries a catalogue price/total and OFD provenance.
        amount_tiyins: The amount actually charged to the card, in tiyins (the caller's
            ``total - points_applied``). Positive and never above the summed line totals.

    Raises:
        ValueError: If the charged amount cannot be reconciled to the line totals.
    """
    full_totals = [int(item.total * 100) for item in order_items]
    sum_full = sum(full_totals)

    if sum_full == amount_tiyins:
        line_totals = full_totals  # card / full-coverage path — nothing to scale
    else:
        if not 0 < amount_tiyins < sum_full:
            raise ValueError(
                f"cannot build OFD items: charged {amount_tiyins} tiyins is outside "
                f"(0, {sum_full}) — the summed line totals"
            )
        line_totals = _scale_line_totals(
            full_totals, amount_tiyins=amount_tiyins, sum_full=sum_full
        )

    ofd_items: list[OFDItem] = []
    for item, line_total in zip(order_items, line_totals, strict=True):
        product = item.product if hasattr(item, "product") else None
        ofd_items.append(
            OFDItem(
                mxik=product.mxik if product and product.mxik else "00000000000000000",
                package_code=product.package_code
                if product and product.package_code
                else "0000000",
                qty=item.quantity,
                price=line_total // item.quantity,
                name=item.product_name[:256],
                total=line_total,
                vat=product.vat if product and product.vat else None,
            )
        )

    # Each line's ``total`` is set from ``line_totals`` verbatim, so this is the fiscal invariant on
    # the document actually emitted: the receipt filed with the tax authority must equal the charge.
    charged = sum(line_totals)
    if charged != amount_tiyins:
        raise ValueError(
            f"OFD line totals sum to {charged} tiyins, expected {amount_tiyins} — refusing to emit "
            f"a fiscal receipt that disagrees with the charge"
        )
    return ofd_items
