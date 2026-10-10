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

    async def delete_invoice(self, uuid: str) -> bool:
        """Annul an UNPAID Multicard invoice (``DELETE /payment/invoice/{uuid}``).

        Returns True ONLY on a confirmed deletion. Multicard answers 200 with
        ``{"success": false, "error": {"code": "ERROR_FIELDS", "details": "Транзакция … завершена"}}``
        when the invoice is already PAID — so the response BODY, not the HTTP status, is what decides.
        Any refusal (already paid / validation) or transport error returns False: the caller must
        then NOT cancel the order, because a still-live invoice could yet be charged. Never raises —
        a lazy cleanup sweep must not break the request that triggered it (mirrors
        :meth:`get_invoice_status`).
        """
        token = await self._get_token()
        url = f"{self.base_url}/payment/invoice/{uuid}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.delete(url, headers=self._get_headers(token))
                result = response.json()
                if result.get("success"):
                    logger.info("Invoice deleted: uuid=%s", uuid)
                    return True
                logger.warning("delete_invoice %s refused: %s", uuid, result.get("error"))
                return False
            except Exception as e:
                logger.error("Failed to delete invoice %s: %s", uuid, e)
                return False

    async def delete_owned_invoice(self, uuid: str) -> bool:
        """Strict, single-UUID annulment for finite inventory; uncertain results stay held."""
        try:
            token = await self._get_token()
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.delete(
                    f"{self.base_url}/payment/invoice/{uuid}", headers=self._get_headers(token)
                )
                response.raise_for_status()
                result = response.json()
                return isinstance(result, dict) and result.get("success") is True
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            logger.warning("Owned invoice annulment unconfirmed: uuid=%s", uuid, exc_info=True)
            return False


# Singleton instance
multicard_client = MultiCardClient()


def _discounted_lines(
    order_items: list[OrderItem], *, amount_tiyins: int, sum_full: int
) -> list[tuple[OrderItem, int, int]]:
    """Spread the points discount across lines, as ``(item, qty, unit_price_tiyins)`` triples.

    Every returned line is self-consistent by construction — the receipt reads its total as
    ``unit_price * qty`` — and the lines together sum to ``amount_tiyins`` to the tiyin. Multicard's
    OFD makes ``price``/``qty`` mandatory and ``total`` optional, so the fiscal receipt is built from
    ``price * qty``, not from a ``total`` we send: an inconsistent line would put a wrong figure on
    the document filed with the tax authority, not merely inside our payload.

    A whole-tiyin unit price cannot express an arbitrary discounted amount on a ``qty > 1`` line
    (107 000 across 3 units is 35 666.66 each), because the reachable per-line sums are multiples of
    ``qty``. So when the last line's discounted total is not divisible by its quantity, it is split
    into ``qty-1`` units at the pro-rata price plus ONE unit carrying the remainder — adding a
    single-unit line makes the amount always exactly reachable, which a single price-per-product
    never can.
    """
    lines: list[list[int]] = []  # [order_items index, qty, unit_price]
    for i, item in enumerate(order_items):
        target = int(item.total * 100) * amount_tiyins // sum_full  # pro-rata line total, floored
        lines.append([i, item.quantity, target // item.quantity])  # floor to a whole unit price
    base = sum(qty * unit_price for _, qty, unit_price in lines)
    residue = amount_tiyins - base  # ≥ 0: both floors only ever undershoot

    idx, qty, unit_price = lines[-1]
    total_last = unit_price * qty + residue
    if total_last % qty == 0:
        lines[-1][2] = total_last // qty  # divisible → keep one line, still exact
    else:
        lines[-1][1] = qty - 1  # qty-1 units at the pro-rata price ...
        lines.append([idx, 1, unit_price + residue])  # ... + 1 unit carrying the remainder

    return [(order_items[i], line_qty, line_price) for i, line_qty, line_price in lines]


def build_ofd_items(order_items: list[OrderItem], *, amount_tiyins: int) -> list[OFDItem]:
    """Build OFD line items whose ``price * qty`` sum EXACTLY to ``amount_tiyins`` (discount model).

    A card order (or a fully points-covered one, which never reaches Multicard) is charged the full
    catalogue sum, so every line passes through at its catalogue unit price — byte-for-byte the
    pre-Stage-6 behaviour. A partially points-covered order is charged only the card remainder: the
    points discount is spread across lines (see :func:`_discounted_lines`), each line kept
    self-consistent (``total == price * qty``) and the whole summing to the charge.

    The receipt is filed with the tax authority, so a fiscal document disagreeing with the sum
    actually charged is a defect: the invariant is enforced with a ``raise`` (it survives
    ``python -O``), never a strippable ``assert``.

    Args:
        order_items: The order's lines — each carries a catalogue price/total and OFD provenance.
        amount_tiyins: The amount charged to the card, in tiyins (the caller's ``total -
            points_applied``). Positive and — today — never above the summed GOODS totals.

    Raises:
        ValueError: If the charged amount cannot be reconciled to the line totals.
    """
    sum_full = sum(int(item.total * 100) for item in order_items)

    if sum_full == amount_tiyins:
        # Card / full-coverage path: catalogue unit prices verbatim. Each line is already
        # self-consistent (``int(total*100) == int(unit_price*100) * qty``).
        lines = [(item, item.quantity, int(item.product_price * 100)) for item in order_items]
    else:
        if not 0 < amount_tiyins < sum_full:
            # ``amount_tiyins`` is the card remainder (total - points) and today never exceeds the
            # GOODS totals because delivery is free (DELIVERY_FEE = 0). A future PAID delivery would
            # make it larger and land here — such a fee needs its OWN ofd line, not a rescale of the
            # goods; fail loud until that line exists rather than silently mis-fiscalise.
            raise ValueError(
                f"cannot build OFD items: charged {amount_tiyins} tiyins is outside "
                f"(0, {sum_full}) — the summed goods totals"
            )
        lines = _discounted_lines(order_items, amount_tiyins=amount_tiyins, sum_full=sum_full)

    ofd_items: list[OFDItem] = []
    for item, qty, unit_price in lines:
        product = item.product if hasattr(item, "product") else None
        ofd_items.append(
            OFDItem(
                mxik=product.mxik if product and product.mxik else "00000000000000000",
                package_code=product.package_code
                if product and product.package_code
                else "0000000",
                qty=qty,
                price=unit_price,
                name=(item.product_name[: 256 - len(f" ({item.size})")] + f" ({item.size})")
                if item.size
                else item.product_name[:256],
                total=unit_price * qty,
                vat=product.vat if product and product.vat else None,
            )
        )

    # The receipt reads each line as price*qty; assert the emitted document equals the charge.
    charged = sum(o.price * o.qty for o in ofd_items)
    if charged != amount_tiyins:
        raise ValueError(
            f"OFD price*qty sum to {charged} tiyins, expected {amount_tiyins} — refusing to emit "
            f"a fiscal receipt that disagrees with the charge"
        )
    return ofd_items
