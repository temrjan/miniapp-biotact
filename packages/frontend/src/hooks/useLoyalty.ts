import { useQuery } from '@tanstack/react-query';

import { loyaltyApi } from '@/api';

// Loyalty snapshot for the current user. While the program is dark the endpoint
// returns 404 — `retry: false` keeps it a single quiet call, and the consumer
// hides its card when `data` is absent (loading / dark / error).
// Exported so the redeem path can invalidate this exact entry after it changes what the snapshot
// reports (a new reservation, or a refused one). Keeping the key in one place stops the two sides
// from drifting into invalidating something nobody reads.
export const LOYALTY_ME_KEY = ['loyalty', 'me'] as const;

export function useLoyalty(enabled = true) {
  return useQuery({
    queryKey: LOYALTY_ME_KEY,
    queryFn: () => loyaltyApi.getMe(),
    retry: false,
    staleTime: 60_000,
    // `enabled: false` — the caller needs the shape but must not fire the request (the legal-docs
    // flag OFF keeps the activation flow byte-identical to pre-PR-2: no extra /me call).
    enabled,
  });
}
