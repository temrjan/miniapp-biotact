import { useQuery } from '@tanstack/react-query';

import { loyaltyApi } from '@/api';

// The member's own lottery participation. While the program is dark the endpoint returns 404 —
// `retry: false` keeps it a single quiet call, and the card hides itself when `data` is absent
// (loading / dark / error), exactly like the loyalty card does.
// Exported so a later PR can invalidate this precise entry when something changes the answer
// (a freeze, a recorded winner). One place for the key stops the two sides from drifting.
export const MY_TICKETS_KEY = ['loyalty', 'my-tickets'] as const;

export function useMyTickets() {
  return useQuery({
    queryKey: MY_TICKETS_KEY,
    queryFn: () => loyaltyApi.getMyTickets(),
    retry: false,
    staleTime: 60_000,
  });
}
