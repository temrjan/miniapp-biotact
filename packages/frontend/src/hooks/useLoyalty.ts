import { useQuery } from '@tanstack/react-query';

import { loyaltyApi } from '@/api';

// Loyalty snapshot for the current user. While the program is dark the endpoint
// returns 404 — `retry: false` keeps it a single quiet call, and the consumer
// hides its card when `data` is absent (loading / dark / error).
export function useLoyalty() {
  return useQuery({
    queryKey: ['loyalty', 'me'],
    queryFn: () => loyaltyApi.getMe(),
    retry: false,
    staleTime: 60_000,
  });
}
