import { useCallback, useEffect, useRef } from 'react';
import WebApp from '@twa-dev/sdk';

import { api, ApiError } from '@/api/client';
import { useCartStore } from '@stores/cartStore';
import { isPendingOrderExpired, usePendingOrderStore } from '@stores/pendingOrderStore';

// ═══════════════════════════════════════════════════════════════════
// Очистка корзины по подтверждённой оплате
// ═══════════════════════════════════════════════════════════════════
//
// Экран «оплата прошла» чистит корзину только тогда, когда открыт ВНУТРИ мини-аппа — то есть при
// полном покрытии баллами. При оплате картой он открывается в браузере, и до корзины мини-аппа не
// дотягивается. Этот хук замыкает разрыв с другой стороны: помнит заказ, за который человек ушёл
// платить (`pendingOrderStore`), и на возврате спрашивает сервер — оплачен?
//
// Хук монтируется в `App` и исполняется у КАЖДОГО открывшего мини-апп, включая тех, кто ничего не
// покупал. Поэтому: без памятки — ни одного запроса, и ни один сбой внутри не имеет права
// вырваться наружу (иначе отказ сверки станет белым экраном для всех).

/**
 * Повторы внутри одного входа, мс от момента срабатывания.
 *
 * Подтверждение от Multicard приходит на сервер асинхронно, уже после того как человека вернули в
 * приложение: в первую секунду сервер честно отвечает «не оплачен». Без повторов корзина висела бы
 * полной до следующего переключения окна — тот же баг в мягкой форме.
 */
const RECHECK_DELAYS_MS = [5000, 15000];

/**
 * HTTP-коды, на которых сервер вынес вердикт по САМОМУ заказу: его нет или он не наш.
 *
 * Список намеренно закрытый. Обобщение «пришёл ApiError ⇒ сервер ответил ⇒ снять памятку»
 * запрещено: 401 (initData живёт 24 часа и протухает ровно в окне жизни памятки) и 408 (наш
 * собственный таймаут, а не ответ сервера) — это «не смогли спросить». Сняв на них памятку, мы бы
 * у оплатившего человека не очистили корзину уже никогда.
 */
const FORGET_ON_HTTP_STATUSES = [403, 404];

/** Платёжные статусы, после которых заказ уже не станет оплаченным. */
const TERMINAL_PAYMENT_STATUSES = ['failed', 'refunded'];

/** Статус заказа, после которого он уже не станет оплаченным (`refunded` приходит вместе с ним). */
const TERMINAL_ORDER_STATUS = 'cancelled';

interface OrderStatusResponse {
  payment_status?: string | null;
  status?: string | null;
}

function isPaid(order: OrderStatusResponse): boolean {
  return order.payment_status === 'paid';
}

function isTerminal(order: OrderStatusResponse): boolean {
  return (
    (order.payment_status != null && TERMINAL_PAYMENT_STATUSES.includes(order.payment_status)) ||
    order.status === TERMINAL_ORDER_STATUS
  );
}

export function useClearCartOnPaidOrder(): void {
  // Гасит дубль, когда два входа сработали разом (возврат фокуса часто даёт и
  // `visibilitychange`, и `activated`).
  const isCheckingRef = useRef(false);

  const check = useCallback(async () => {
    if (isCheckingRef.current) {
      return;
    }

    const { pending, forgetOrder } = usePendingOrderStore.getState();
    if (!pending) {
      return;
    }
    if (isPendingOrderExpired(pending)) {
      forgetOrder();
      return;
    }

    isCheckingRef.current = true;
    try {
      const order = await api.get<OrderStatusResponse>(`/orders/${pending.orderId}`);

      if (isPaid(order)) {
        useCartStore.getState().clearCart();
        forgetOrder();
        return;
      }

      if (isTerminal(order)) {
        forgetOrder();
      }
      // Иначе заказ ещё в пути (`unpaid` / `pending`) — памятку держим, повторим позже.
    } catch (error: unknown) {
      if (error instanceof ApiError && FORGET_ON_HTTP_STATUSES.includes(error.status)) {
        forgetOrder();
        return;
      }
      // 401, 408, 5xx, обрыв сети, неожиданное тело — «не смогли спросить». Памятку оставляем,
      // экран не трогаем: сверка не имеет права ломать приложение тем, кто просто его открыл.
    } finally {
      isCheckingRef.current = false;
    }
  }, []);

  useEffect(() => {
    let recheckTimers: ReturnType<typeof setTimeout>[] = [];

    const clearRecheckTimers = () => {
      for (const timer of recheckTimers) {
        clearTimeout(timer);
      }
      recheckTimers = [];
    };

    const run = () => {
      // Не покупал — нет памятки — нет ни запроса, ни таймеров.
      if (!usePendingOrderStore.getState().pending) {
        return;
      }

      // Повторы прошлого входа отменяем: этот вход их заменяет.
      clearRecheckTimers();
      void check();
      recheckTimers = RECHECK_DELAYS_MS.map((delay) =>
        setTimeout(() => {
          void check();
        }, delay)
      );
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        run();
      }
    };

    // Вход №1 — старт приложения. Единственный, который сработает, если мини-апп был выгружен,
    // пока человек платил в браузере.
    run();

    // Вход №2 — возврат видимости. Веб-стандарт, работает в любом WebView.
    document.addEventListener('visibilitychange', handleVisibilityChange);

    // Вход №3 — событие Telegram (Bot API 8.0) на случай, когда WebView остаётся «видимым» за
    // окном браузера. На клиентах старше 8.0 просто молчит — тогда работают входы №1 и №2.
    WebApp.onEvent('activated', run);

    return () => {
      clearRecheckTimers();
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      WebApp.offEvent('activated', run);
    };
  }, [check]);
}
