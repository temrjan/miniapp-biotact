import type { CartItem } from '@/types';
import { effectivePrice } from '@/utils/price';

/** Money in whole tiyin (1/100 sum), so the R-10 rule runs on exact integers. */
function toCents(value: number): number {
  return Math.round(value * 100);
}

/**
 * Promotion unit price in tiyin: `price × (100 − percent) / 100`, rounded half up to a whole sum
 * (spec §2). `cents × (100 − percent)` is an exact integer, and one whole sum is 10 000 of it.
 */
function promotionUnitCents(priceCents: number, percent: number): number {
  return Math.floor((priceCents * (100 - percent) + 5000) / 10000) * 100;
}

export interface CartDiscount {
  /** Sum of every line at its catalogue effective price. */
  subtotal: number;
  /** What the promotion takes off, per R-10: only lines whose promotion price is strictly lower. */
  discount: number;
  /** `subtotal − discount`: the amount the server quote charges for this cart. */
  total: number;
  /** At least one line keeps its own price because it is already at or below the promotion price. */
  hasUndiscounted: boolean;
}

/**
 * The cart under the R-10 best-of rule, mirrored for display (event-qr-discount R-51): each line
 * costs the lower of its catalogue effective price and the promotion unit price of its regular
 * price. The server quote stays authoritative at checkout (R-52).
 */
export function cartDiscount(items: CartItem[], percent: number): CartDiscount {
  let subtotal = 0;
  let discount = 0;
  let hasUndiscounted = false;
  for (const item of items) {
    const unit = toCents(effectivePrice(item.product));
    const promoted = promotionUnitCents(toCents(item.product.price), percent);
    subtotal += unit * item.quantity;
    if (promoted < unit) discount += (unit - promoted) * item.quantity;
    else hasUndiscounted = true;
  }
  return {
    subtotal: subtotal / 100,
    discount: discount / 100,
    total: (subtotal - discount) / 100,
    hasUndiscounted,
  };
}

/**
 * The last valid Tashkent day of a promotion that ends at `endsAt` (exclusive), as `DD.MM.YYYY` —
 * the same date the bot shows (`ends_at − 1 µs` in Asia/Tashkent).
 */
export function lastPromotionDay(endsAt: Date): string {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Tashkent',
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  }).formatToParts(new Date(endsAt.getTime() - 1));
  const part = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((entry) => entry.type === type)?.value ?? '';
  return `${part('day')}.${part('month')}.${part('year')}`;
}
