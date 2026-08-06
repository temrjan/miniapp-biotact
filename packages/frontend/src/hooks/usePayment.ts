import { useCallback, useRef, useState } from 'react';
import WebApp from '@twa-dev/sdk';
import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import { useHaptic } from '@hooks/useTelegram';
import { LOYALTY_ME_KEY } from '@hooks/useLoyalty';
import { usePendingOrderStore } from '@stores/pendingOrderStore';
import { useTranslation } from '@/i18n';
import { api, ApiError } from '@/api/client';
import { buildOrderRequest } from '@/utils/order';
import { ROUTES } from '@/utils/constants';
import type { CartItem } from '@/types';

interface OrderResponse {
  id: number;
  order_number: string;
  total: number;
  // Since Stage 6: full points coverage returns the order already `paid` (no Multicard leg);
  // `points_applied` is how many points were spent (0 for a pure card order).
  payment_status: string;
  points_applied: number;
}

interface PaymentResponse {
  payment_url: string;
  payment_uuid: string;
  order_id: number;
}

/** Safely read ``detail.max_redeemable`` from a 422 body (`InvalidRedeemAmountError`), or null. */
function readMaxRedeemable(data: unknown): number | null {
  if (typeof data !== 'object' || data === null || !('detail' in data)) return null;
  const detail = (data as { detail?: unknown }).detail;
  if (typeof detail !== 'object' || detail === null || !('max_redeemable' in detail)) return null;
  const max = (detail as { max_redeemable?: unknown }).max_redeemable;
  return typeof max === 'number' ? max : null;
}

interface UsePaymentOptions {
  items: CartItem[];
  deliveryAddress: string | null;
  name: string | null;
  phone: string;
  /** Points to spend on this order — already gated/clamped by the caller (0 = card-only). */
  redeemPoints: number;
  /** Called with the server's ``max_redeemable`` when a 422 clamps the amount (null if unreadable). */
  onInvalidAmount: (max: number | null) => void;
}

/**
 * Create-order → pay flow with a SYNCHRONOUS re-entry guard.
 *
 * The guard (`isProcessingRef`) flips in the same tick, BEFORE any await: `setIsProcessing` (React
 * render) and the native ``MainButton.disable()`` (JS→native bridge round-trip) both settle
 * asynchronously, so a fast double-tap could otherwise enter twice and — post-Stage-6 — create two
 * orders plus a double points debit (two distinct order_ids; the ledger's `UNIQUE(order_id, reason)`
 * does not help). Full points coverage settles server-side and skips Multicard; a partial/card order
 * goes to the gateway for the remainder.
 */
export function usePayment({
  items,
  deliveryAddress,
  name,
  phone,
  redeemPoints,
  onInvalidAmount,
}: UsePaymentOptions) {
  const navigate = useNavigate();
  const haptic = useHaptic();
  const queryClient = useQueryClient();
  const { t } = useTranslation();
  const [isProcessing, setIsProcessing] = useState(false);
  const isProcessingRef = useRef(false);
  const rememberOrder = usePendingOrderStore((state) => state.rememberOrder);

  const pay = useCallback(async () => {
    if (isProcessingRef.current) return;
    isProcessingRef.current = true;
    setIsProcessing(true);
    haptic.success();
    try {
      const orderRequest = buildOrderRequest(items, { deliveryAddress, phone, name }, redeemPoints);
      const order = await api.post<OrderResponse>('/orders', orderRequest);

      // Full points coverage is settled server-side (paid + confirmed) — no Multicard leg. Reuse the
      // existing success page (it clears the cart on mount); calling /payments/create would 400.
      if (order.payment_status === 'paid') {
        navigate(`${ROUTES.PAYMENT_SUCCESS}?order_id=${order.order_number}`);
        return;
      }

      const returnUrl = window.location.origin + '/payment/success?order_id=' + order.order_number;
      const payment = await api.post<PaymentResponse>('/payments/create', {
        order_id: order.id,
        return_url: returnUrl,
      });
      // The order above already RESERVED the points, so the cached snapshot is now stale. Refresh
      // it before handing the customer to the gateway: `refetchOnWindowFocus` is off app-wide
      // (App.tsx), so nothing re-reads it when they come back from an abandoned payment — which is
      // exactly when they look at their balance and try to redeem again.
      void queryClient.invalidateQueries({ queryKey: LOYALTY_ME_KEY });
      // Отсюда человек уходит из мини-аппа в браузер, и экран «оплата прошла» — вместе с его
      // очисткой корзины — откроется ТАМ. Запоминаем заказ, чтобы очистить корзину у себя, когда
      // человек вернётся (`useClearCartOnPaidOrder`). Запоминаем `order.id` — числовой, тот, что
      // принимает `GET /orders/{order_id}`; `order_number` из адреса возврата здесь не годится.
      rememberOrder(order.id);
      WebApp.openLink(payment.payment_url);
    } catch (error) {
      // Failed — release the guard so the customer can retry.
      isProcessingRef.current = false;
      setIsProcessing(false);
      haptic.error();
      // 422 = the amount broke a redeem rule (e.g. balance drained since the form read): clamp the
      // stepper to the server's max so the customer can retry. 409 = live-balance shortfall.
      if (error instanceof ApiError && error.status === 422) {
        onInvalidAmount(readMaxRedeemable(error.data));
        alert(t('checkout.redeem.invalidAmount'));
        return;
      }
      if (error instanceof ApiError && error.status === 409) {
        // The server refused against a reserve-aware balance the cache does not know about — refresh
        // so the stepper clamps to the truth and the reserve notice appears instead of a bare alert.
        void queryClient.invalidateQueries({ queryKey: LOYALTY_ME_KEY });
        alert(t('checkout.redeem.insufficient'));
        return;
      }
      console.error('Payment error:', error);
      alert(error instanceof Error ? error.message : t('common.error'));
    }
  }, [
    items,
    deliveryAddress,
    name,
    phone,
    redeemPoints,
    onInvalidAmount,
    navigate,
    haptic,
    queryClient,
    rememberOrder,
    t,
  ]);

  return { pay, isProcessing };
}
