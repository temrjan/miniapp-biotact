import { useQuery } from '@tanstack/react-query';
import { referralReturnsApi } from '../api/referralReturnsApi';

export const useReferralReturns = (orderId: string) => useQuery({
  queryKey: ['referral-returns', orderId],
  queryFn: () => referralReturnsApi.summary(orderId),
  retry: false,
  staleTime: 0,
});
