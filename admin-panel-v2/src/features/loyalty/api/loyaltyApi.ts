import { apiClient } from '@/shared/api'
import type { LoyaltyStats } from '../types'

export const loyaltyApi = {
  /**
   * Get Biotact Club loyalty monitoring stats (read-only, admin-only).
   * @param days - Window for the QR-activation trend chart (default: 30).
   *                Scopes ONLY `activations_by_day`; all other blocks are all-time or fixed 7/30d.
   */
  getLoyaltyStats: async (days: number = 30): Promise<LoyaltyStats> => {
    const response = await apiClient.get<LoyaltyStats>('/analytics/loyalty', {
      params: { days },
    })
    return response.data
  },
}
