import { useQuery } from '@tanstack/react-query'
import { loyaltyApi } from '../api/loyaltyApi'

export const useLoyaltyStats = (days: number = 30) => {
  return useQuery({
    queryKey: ['loyalty', 'stats', days],
    queryFn: () => loyaltyApi.getLoyaltyStats(days),
    staleTime: 1000 * 60 * 5, // 5 minutes
  })
}
