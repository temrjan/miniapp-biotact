"""Multicard payment service."""

import hashlib
import logging
from datetime import datetime, timedelta

import httpx

from src.config import settings
from src.payments.schemas import (
    MultiCardPaymentResponse,
    OFDItem,
    PaymentCreateData,
)

logger = logging.getLogger(__name__)


class MultiCardClient:
    """Multicard API client."""

    def __init__(self):
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

    def _get_headers(self, token: str) -> dict:
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
        # MD5: {store_id}{invoice_id}{amount}{secret}
        data = f"{store_id}{invoice_id}{amount}{self.secret}"
        expected_sign = hashlib.md5(data.encode()).hexdigest()
        return sign == expected_sign

    def verify_webhook_sign(
        self,
        uuid: str,
        invoice_id: str,
        amount: int,
        sign: str,
    ) -> bool:
        """Verify webhook signature (SHA1)."""
        # SHA1: {uuid}{invoice_id}{amount}{secret}
        data = f"{uuid}{invoice_id}{amount}{self.secret}"
        expected_sign = hashlib.sha1(data.encode()).hexdigest()
        return sign == expected_sign

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

        logger.info(f"Creating invoice: invoice_id={payment_data.invoice_id}, amount={payment_data.amount}")

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

    async def get_invoice_status(self, uuid: str) -> dict | None:
        """Get invoice status from Multicard."""
        token = await self._get_token()
        url = f"{self.base_url}/payment/invoice/{uuid}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(url, headers=self._get_headers(token))
                response.raise_for_status()
                result = response.json()
                if result.get("success"):
                    return result.get("data")
                return None
            except Exception as e:
                logger.error(f"Failed to get invoice status: {e}")
                return None


# Singleton instance
multicard_client = MultiCardClient()


def build_ofd_items(order_items: list) -> list[OFDItem]:
    """Build OFD items from order items."""
    ofd_items = []

    for item in order_items:
        # Price in tiyins (sum * 100)
        price_tiyins = int(item.product_price * 100)
        total_tiyins = int(item.total * 100)

        # Get OFD data from product
        product = item.product if hasattr(item, 'product') else None

        ofd_item = OFDItem(
            mxik=product.mxik if product and product.mxik else "00000000000000000",
            package_code=product.package_code if product and product.package_code else "0000000",
            qty=item.quantity,
            price=price_tiyins,
            name=item.product_name[:256],
            total=total_tiyins,
            vat=product.vat if product and product.vat else None,
        )
        ofd_items.append(ofd_item)

    return ofd_items
