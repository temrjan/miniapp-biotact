import type { CartItem } from '@/types';

export interface OrderLine {
  product_id: number;
  quantity: number;
}

/**
 * What we ask the server to create. It carries NO price: the server prices every line from the
 * catalogue and computes the totals itself. A client-declared price was the hole closed in #79.
 *
 * ``points_to_apply`` is the AMOUNT of Biotact Club points to spend (not a price): 0 = card-only,
 * = subtotal covers the goods in full, between = partial (Stage 6). Validated server-side against
 * the redeem rules; always present (default 0) so the field is unambiguous.
 */
export interface CreateOrderRequest {
  items: OrderLine[];
  points_to_apply: number;
  delivery_address: string | null;
  delivery_phone: string | null;
  delivery_name: string | null;
  customer_note: string | null;
}

/** Server-calculated checkout terms; money stays in the wire decimal format. */
export interface OrderQuote {
  subtotal: string;
  delivery_fee: string;
  total: string;
  max_redeemable: number;
  points_to_apply: number;
  quote_fingerprint: string;
}

export interface DeliveryDetails {
  deliveryAddress: string | null;
  phone: string | null;
  name: string | null;
}

/**
 * Build the create-order request from the cart.
 *
 * Lives here rather than inside CheckoutPage so the "no money in this payload" contract can be
 * pinned by a test without dragging in Telegram WebApp, the router and the stores the page needs
 * (and so the page file keeps exporting only its component — react-refresh).
 */
export function buildOrderRequest(
  items: CartItem[],
  { deliveryAddress, phone, name }: DeliveryDetails,
  pointsToApply = 0
): CreateOrderRequest {
  return {
    // Identity and quantity only — the catalogue is the sole source of price.
    items: items.map((item) => ({
      product_id: parseInt(item.productId, 10),
      quantity: item.quantity,
    })),
    points_to_apply: pointsToApply,
    delivery_address: deliveryAddress,
    delivery_phone: phone,
    delivery_name: name,
    customer_note: null,
  };
}
