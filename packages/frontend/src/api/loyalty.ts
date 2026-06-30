import { api } from './client';
import type { LoyaltySummary, LoyaltyTier } from '@/types';

// ═══════════════════════════════════════════════════════════════════
// Loyalty API (Biotact Club)
// ═══════════════════════════════════════════════════════════════════

// Raw server shape (snake_case); mapped to the camelCase domain type below.
interface LoyaltySummaryDto {
  tier: LoyaltyTier;
  balance: number;
  lifetime_activations: number;
  activations_to_friend: number;
}

export const loyaltyApi = {
  // GET /loyalty/me — current user's loyalty snapshot. 404 while the program is dark.
  getMe: async (): Promise<LoyaltySummary> => {
    const dto = await api.get<LoyaltySummaryDto>('/loyalty/me');
    return {
      tier: dto.tier,
      balance: dto.balance,
      lifetimeActivations: dto.lifetime_activations,
      activationsToFriend: dto.activations_to_friend,
    };
  },
};
