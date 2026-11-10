import { api } from './client';

// ═══════════════════════════════════════════════════════════════════
// Event promotion API (event-qr-discount R-20)
// ═══════════════════════════════════════════════════════════════════

/** The buyer's active event discount, as the server selected it for pricing. */
export interface EventPromotion {
  title: string;
  percent: number;
  endsAt: Date;
}

/**
 * Runtime check of the `GET /event-promotions/me` body (spec §6: the R-20 response is validated on
 * the client). `{"active": false}`, and any body that does not carry a usable active grant, is
 * `null`: the Mini App then shows ordinary prices and the server quote stays the authority.
 */
export function parseEventPromotion(data: unknown): EventPromotion | null {
  if (typeof data !== 'object' || data === null || !('active' in data) || data.active !== true) {
    return null;
  }
  if (!('title' in data) || typeof data.title !== 'string' || data.title.length === 0) return null;
  if (
    !('percent' in data) ||
    typeof data.percent !== 'number' ||
    !Number.isInteger(data.percent) ||
    data.percent < 1 ||
    data.percent > 90
  ) {
    return null;
  }
  if (!('ends_at' in data) || typeof data.ends_at !== 'string') return null;
  const endsAt = new Date(data.ends_at);
  if (Number.isNaN(endsAt.getTime())) return null;
  return { title: data.title, percent: data.percent, endsAt };
}

export const eventPromotionsApi = {
  // GET /event-promotions/me — never cached by the server; `{"active": false}` while the switch is OFF.
  getMine: async (): Promise<EventPromotion | null> =>
    parseEventPromotion(await api.get<unknown>('/event-promotions/me')),
};
