import type { CartItem } from '@/types';

export interface OrderLine {
  product_id: number;
  quantity: number;
}

/**
 * What we ask the server to create. It carries NO money: the server prices every line from the
 * catalogue and computes the totals itself. A client-declared price was the hole closed in #79.
 */
export interface CreateOrderRequest {
  items: OrderLine[];
  delivery_address: string | null;
  delivery_phone: string | null;
  delivery_name: string | null;
  customer_note: string | null;
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
  { deliveryAddress, phone, name }: DeliveryDetails
): CreateOrderRequest {
  return {
    // Identity and quantity only — the catalogue is the sole source of price.
    items: items.map((item) => ({
      product_id: parseInt(item.productId, 10),
      quantity: item.quantity,
    })),
    delivery_address: deliveryAddress,
    delivery_phone: phone,
    delivery_name: name,
    customer_note: null,
  };
}
