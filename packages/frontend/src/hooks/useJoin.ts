import { useMutation, useQueryClient } from '@tanstack/react-query';

import { loyaltyApi } from '@/api';
import { isLegalEnabled } from '@/legal/flag';
import { buildConsentVersion } from '@/legal';
import { useConsentStore } from '@/stores/consentStore';
import type { ConsentResult } from '@/types';

export interface JoinVariables {
  // The optional marketing checkbox ticked at the join gate (flag ON only).
  marketing: boolean;
}

// Direct join (no QR): record data-processing consent with source=direct, no points accrued.
// Idempotent server-side. On success the loyalty summary is invalidated so the profile swaps
// from the join CTA to the loyalty card (is_member flips true).
//
// Flag ON: consent carries the legal-docs edition, and a ticked marketing box is granted via its
// own call AFTER consent (the grant is member-only; consent creates the membership). Flag OFF:
// the exact pre-PR-2 request — no version, no marketing call.
export function useJoin() {
  const queryClient = useQueryClient();
  const resetConsent = useConsentStore((state) => state.reset);

  return useMutation<ConsentResult, Error, JoinVariables>({
    mutationFn: async ({ marketing }) => {
      const docVersion = isLegalEnabled() ? buildConsentVersion() : undefined;
      const result = await loyaltyApi.consent('direct', docVersion);
      if (marketing) {
        await loyaltyApi.setMarketingConsent(true, docVersion);
      }
      return result;
    },
    onSuccess: () => {
      resetConsent();
      void queryClient.invalidateQueries({ queryKey: ['loyalty', 'me'] });
    },
  });
}
