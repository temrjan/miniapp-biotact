"""Payment schemas for Multicard integration."""


from pydantic import BaseModel, Field


# OFD Item for Invoice
class OFDItem(BaseModel):
    """OFD item for fiscal receipt."""
    mxik: str = Field(..., max_length=17, description="IKPU code")
    package_code: str = Field(..., max_length=7, description="Package code")
    qty: int = Field(..., ge=1, description="Quantity")
    price: int = Field(..., ge=0, description="Price in tiyins")
    name: str = Field(..., max_length=256, description="Product name")
    total: int | None = Field(None, description="Total in tiyins")
    vat: int | None = Field(None, ge=0, le=100, description="VAT percent")


# Request to create payment
class CreatePaymentRequest(BaseModel):
    """Request to create payment on Multicard."""
    order_id: int = Field(..., description="Order ID")
    return_url: str = Field(..., description="URL to redirect after payment")


# Internal payment creation data
class PaymentCreateData(BaseModel):
    """Data for creating invoice in Multicard API."""
    store_id: str
    amount: int = Field(..., description="Amount in tiyins")
    invoice_id: str = Field(..., description="Unique invoice ID (order_number)")
    callback_url: str
    return_url: str
    lang: str = "ru"
    ofd: list[OFDItem]


# Multicard API Response
class MultiCardPaymentResponse(BaseModel):
    """Response from Multicard invoice creation."""
    uuid: str
    pay_url: str


# Success callback from Multicard (POST to callback_url)
class SuccessCallbackData(BaseModel):
    """Success callback data from Multicard."""
    store_id: int
    amount: int
    invoice_id: str
    billing_id: str | None = None
    payment_time: str | None = None
    phone: str | None = None
    card_pan: str | None = None
    ps: str | None = None  # Payment system: uzcard, humo, etc.
    card_token: str | None = None
    uuid: str
    receipt_url: str | None = None
    sign: str  # MD5: {store_id}{invoice_id}{amount}{secret}


# Response to frontend
class PaymentResponse(BaseModel):
    """Payment response to frontend."""
    payment_url: str
    payment_uuid: str
    order_id: int
