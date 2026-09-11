import { useMutation, useQueryClient } from '@tanstack/react-query';

import { ApiError, loyaltyApi } from '@/api';
import { isLegalEnabled } from '@/legal/flag';
import { buildConsentVersion } from '@/legal';
import { useConsentStore } from '@/stores/consentStore';
import type { ActivationResult } from '@/types';

// Raised when the consent call 404s — the program is dark (the `require_loyalty_enabled` gate
// fires before any logic, so a dark surface is not probeable). This is a DIFFERENT outcome from a
// 404 on `activate`, which — while the program is live — means the scanned code is unknown. The
// page maps each to its own message ("coming soon" vs "invalid code"). Keeping the two apart on the
// client is possible only because consent runs first: a dark program short-circuits here.
export class ProgramDarkError extends Error {
  constructor(cause: ApiError) {
    super('Loyalty program is not available', { cause });
    this.name = 'ProgramDarkError';
  }
}

export interface ActivationVariables {
  code: string;
  // The optional marketing checkbox the member ticked at the consent gate (flag ON only).
  marketing: boolean;
}

// The QR activation flow: record data-processing consent (source=qr), then claim the code.
// Both server calls are idempotent, so a retry after a network error is safe. On success the
// loyalty summary is invalidated so the profile card reflects the new balance/tier.
//
// Flag ON (VITE_LEGAL_DOCS): the consent call carries the legal-docs edition from the client
// registry, and a ticked marketing box is recorded via its own grant call AFTER consent (the
// grant is member-only, and consent is what creates the membership). Flag OFF: the exact
// pre-PR-2 request shape — no version, no marketing call.
export function useActivation() {
  const queryClient = useQueryClient();
  const resetConsent = useConsentStore((state) => state.reset);

  return useMutation<ActivationResult, Error, ActivationVariables>({
    mutationFn: async ({ code, marketing }) => {
      const docVersion = isLegalEnabled() ? buildConsentVersion() : undefined;
      try {
        await loyaltyApi.consent('qr', docVersion);
      } catch (error) {
        // A 404 here can only mean the program is dark; surface it as ProgramDarkError so the UI
        // shows "coming soon", not "invalid code". Every other failure (503/401/network) — and any
        // 404 from `activate` below — propagates unchanged for status-based mapping.
        if (error instanceof ApiError && error.status === 404) {
          throw new ProgramDarkError(error);
        }
        throw error;
      }
      if (marketing) {
        // Idempotent server-side; a failure propagates and the whole (idempotent) flow can retry.
        await loyaltyApi.setMarketingConsent(true, docVersion);
      }
      return loyaltyApi.activate(code);
    },
    onSuccess: () => {
      resetConsent();
      void queryClient.invalidateQueries({ queryKey: ['loyalty', 'me'] });
    },
  });
}
