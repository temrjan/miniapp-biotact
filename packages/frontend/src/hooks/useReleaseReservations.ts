import { useMutation, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import { LOYALTY_ME_KEY } from '@hooks/useLoyalty';

export interface ReleaseResult {
  released_orders: number;
  released_points: number;
  blocked_orders: number;
}

/**
 * «Вернуть баллы» — освобождает баллы, удерживаемые собственными незавершёнными заказами.
 *
 * Без этого клиент ждёт до 30 минут: ленивая очистка на сервере выдерживает паузу, потому что не
 * знает, платит человек прямо сейчас или бросил. Здесь человек сказал сам.
 *
 * Снимок лояльности инвалидируется в ЛЮБОМ исходе, включая ошибку: 409 означает «освободить не
 * удалось», а не «состояние прежнее» — счёт мог быть оплачен, и тогда сервер уже видит другую
 * картину, чем закэшированная.
 */
export function useReleaseReservations() {
  const queryClient = useQueryClient();

  return useMutation<ReleaseResult, unknown, void>({
    mutationFn: () => api.post<ReleaseResult>('/orders/reservations/release', {}),
    onSettled: () => queryClient.invalidateQueries({ queryKey: LOYALTY_ME_KEY }),
  });
}
