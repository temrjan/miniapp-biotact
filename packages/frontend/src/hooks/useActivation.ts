import { useMutation, useQueryClient } from '@tanstack/react-query';

import { loyaltyApi } from '@/api';
import type { ActivationResult } from '@/types';

// The QR activation flow: record data-processing consent (source=qr), then claim the code.
// Both server calls are idempotent, so a retry after a network error is safe. On success the
// loyalty summary is invalidated so the profile card reflects the new balance/tier.
export function useActivation() {
  const queryClient = useQueryClient();

  return useMutation<ActivationResult, Error, string>({
    mutationFn: async (code: string) => {
      await loyaltyApi.consent('qr');
      return loyaltyApi.activate(code);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['loyalty', 'me'] });
    },
  });
}
