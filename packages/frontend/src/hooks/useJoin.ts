import { useMutation, useQueryClient } from '@tanstack/react-query';

import { loyaltyApi } from '@/api';
import type { ConsentResult } from '@/types';

// Direct join (no QR): record data-processing consent with source=direct, no points accrued.
// Idempotent server-side. On success the loyalty summary is invalidated so the profile swaps
// from the join CTA to the loyalty card (is_member flips true).
export function useJoin() {
  const queryClient = useQueryClient();

  return useMutation<ConsentResult, Error, void>({
    mutationFn: () => loyaltyApi.consent('direct'),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['loyalty', 'me'] });
    },
  });
}
