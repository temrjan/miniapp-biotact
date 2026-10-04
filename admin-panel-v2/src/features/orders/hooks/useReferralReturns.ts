import { useQuery, useQueryClient } from '@tanstack/react-query';
import { isAxiosError } from 'axios';
import { referralReturnsApi } from '../api/referralReturnsApi';
import type { ReturnSummary } from '../api/referralReturnsApi';

export const useReferralReturns = (orderId: string) => {
  const client = useQueryClient();
  const queryKey = ['referral-returns', orderId];
  const summary = useQuery({
    queryKey,
    queryFn: async (): Promise<ReturnSummary | null> => {
      try {
        return await referralReturnsApi.summary(orderId);
      } catch (error) {
        if (isAxiosError(error) && (error.response?.status === 401 || error.response?.status === 403)) {
          // Replace any cached protected summary with an access-refusal marker.
          return null;
        }
        throw error;
      }
    },
    retry: false,
    staleTime: 0,
  });
  const clearProtectedData = () => {
    // Cancel first so an already pending read cannot restore data after refusal.
    void client.cancelQueries({ queryKey, exact: true });
    client.setQueryData<ReturnSummary | null>(queryKey, null);
  };
  return { ...summary, clearProtectedData };
};
