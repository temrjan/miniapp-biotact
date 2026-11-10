import { useQuery } from '@tanstack/react-query';

import { eventPromotionsApi } from '@/api/eventPromotions';

export const EVENT_PROMOTION_KEY = ['event-promotions', 'me'] as const;

/**
 * The buyer's active event discount (event-qr-discount R-20), shared by the banner, the cart and
 * checkout. `data` is `null` without an active grant and `undefined` while loading or after an
 * error; consumers treat both as "no discount shown".
 */
export function useEventPromotion() {
  return useQuery({
    queryKey: EVENT_PROMOTION_KEY,
    queryFn: () => eventPromotionsApi.getMine(),
    staleTime: 60_000,
  });
}
