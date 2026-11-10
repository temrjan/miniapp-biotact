import type { CartItem } from '@/types';

export interface OrderLine {
  product_id: number;
  quantity: number;
  size?: string;
}

/** Compare confirmed order lines before clearing a size basket that may have been edited. */
export function cartMatchesOrder(cart: CartItem[], lines: unknown): boolean {
  if (!Array.isArray(lines)) return false;
  const rawLines: unknown[] = lines;
  const ordered = new Map<string, number>();
  for (const line of rawLines) {
    if (typeof line !== 'object' || line === null ||
      !('product_id' in line) || typeof line.product_id !== 'number' || !Number.isInteger(line.product_id) ||
      !('quantity' in line) || typeof line.quantity !== 'number' || !Number.isInteger(line.quantity) || line.quantity < 1 ||
      ('size' in line && line.size != null && typeof line.size !== 'string')) return false;
    const key = JSON.stringify([String(line.product_id), 'size' in line ? line.size ?? null : null]);
    ordered.set(key, (ordered.get(key) ?? 0) + line.quantity);
  }
  const current = new Map<string, number>();
  for (const line of cart) {
    const key = JSON.stringify([line.productId, line.size ?? null]);
    current.set(key, (current.get(key) ?? 0) + line.quantity);
  }
  return current.size === ordered.size && [...current].every(([key, quantity]) => ordered.get(key) === quantity);
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
  /** Event discount taken off this quote (event-qr-discount R-13); present only with an active grant. */
  promotion_discount?: string;
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
      ...(item.size ? { size: item.size } : {}),
    })),
    points_to_apply: pointsToApply,
    delivery_address: deliveryAddress,
    delivery_phone: phone,
    delivery_name: name,
    customer_note: null,
  };
}
