import { useMutation, useQueryClient } from '@tanstack/react-query';

import { loyaltyApi } from '@/api';
import { isLegalEnabled } from '@/legal/flag';
import { buildConsentVersion } from '@/legal';
import type { MarketingConsentResult } from '@/types';
import { LOYALTY_ME_KEY } from './useLoyalty';

// Marketing grant/revoke from the profile "Согласия" block (member-only, legal-docs PR-2).
// The response carries the resulting state; the summary invalidation makes the toggle's
// server truth converge everywhere it is read.
export function useMarketingConsent() {
  const queryClient = useQueryClient();

  return useMutation<MarketingConsentResult, Error, boolean>({
    mutationFn: (granted) =>
      loyaltyApi.setMarketingConsent(
        granted,
        isLegalEnabled() ? buildConsentVersion() : undefined
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: LOYALTY_ME_KEY });
    },
  });
}
