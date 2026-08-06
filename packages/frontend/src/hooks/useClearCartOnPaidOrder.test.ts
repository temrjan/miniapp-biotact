import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import WebApp from '@twa-dev/sdk';

import { api, ApiError } from '@/api/client';
import type { Product } from '@/types';
import { useCartStore } from '@stores/cartStore';
import { PENDING_ORDER_TTL_MS, usePendingOrderStore } from '@stores/pendingOrderStore';
import { useClearCartOnPaidOrder } from './useClearCartOnPaidOrder';

// api client → get() spy + ApiError со статусом, как у настоящего.
vi.mock('@/api/client', () => {
  class ApiError extends Error {
    status: number;
    data?: unknown;
    constructor(message: string, status: number, data?: unknown) {
      super(message);
      this.status = status;
      this.data = data;
    }
  }
  return { ApiError, api: { get: vi.fn(), post: vi.fn() } };
});

const getMock = vi.mocked(api.get);
const onEventMock = vi.mocked(WebApp.onEvent);
const offEventMock = vi.mocked(WebApp.offEvent);

const product = { id: '7', name: 'X', price: 30000 } as Product;

/** Достаёт обработчик, которым хук подписался на телеграмное событие `activated`. */
function activatedHandler(): () => void {
  const call = onEventMock.mock.calls.find(([event]) => event === 'activated');
  if (!call) {
    throw new Error('хук не подписался на activated');
  }
  return call[1] as () => void;
}

/** Имитация возврата видимости — вход №2. */
function fireVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
  document.dispatchEvent(new Event('visibilitychange'));
}

function withCartAndPending() {
  useCartStore.getState().addItem(product);
  usePendingOrderStore.getState().rememberOrder(42);
}

let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  getMock.mockReset();
  onEventMock.mockReset();
  offEventMock.mockReset();
  useCartStore.getState().clearCart();
  usePendingOrderStore.getState().forgetOrder();
  fireVisibility('visible');
  consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useClearCartOnPaidOrder', () => {
  it('старт приложения + заказ оплачен → корзина пуста, памятка снята', async () => {
    withCartAndPending();
    getMock.mockResolvedValue({ payment_status: 'paid', status: 'confirmed' });

    await act(async () => {
      renderHook(() => useClearCartOnPaidOrder());
    });

    expect(getMock).toHaveBeenCalledWith('/orders/42');
    expect(useCartStore.getState().items).toHaveLength(0);
    expect(usePendingOrderStore.getState().pending).toBeNull();
  });

  it('заказ ещё не оплачен → корзина цела, памятка жива, проверка повторяется', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    withCartAndPending();
    getMock.mockResolvedValue({ payment_status: 'unpaid', status: 'pending' });

    await act(async () => {
      renderHook(() => useClearCartOnPaidOrder());
    });

    expect(getMock).toHaveBeenCalledTimes(1);
    expect(useCartStore.getState().items).toHaveLength(1);
    expect(usePendingOrderStore.getState().pending).not.toBeNull();

    // Подтверждение от Multicard могло дойти уже после возврата человека — повтор обязан быть.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(getMock).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(getMock).toHaveBeenCalledTimes(3);
  });

  it.each([
    ['failed', { payment_status: 'failed', status: 'cancelled' }],
    ['refunded', { payment_status: 'refunded', status: 'cancelled' }],
    ['cancelled', { payment_status: 'unpaid', status: 'cancelled' }],
  ])('терминальный заказ (%s) → памятка снята, корзина цела', async (_name, response) => {
    withCartAndPending();
    getMock.mockResolvedValue(response);

    await act(async () => {
      renderHook(() => useClearCartOnPaidOrder());
    });

    expect(usePendingOrderStore.getState().pending).toBeNull();
    expect(useCartStore.getState().items).toHaveLength(1);
  });

  it.each([403, 404])(
    'HTTP %i (заказ не наш / его нет) → памятка снята, корзина цела',
    async (status) => {
      withCartAndPending();
      getMock.mockRejectedValue(new ApiError('nope', status));

      await act(async () => {
        renderHook(() => useClearCartOnPaidOrder());
      });

      expect(usePendingOrderStore.getState().pending).toBeNull();
      expect(useCartStore.getState().items).toHaveLength(1);
      // Штатный исход — в консоль не шумим.
      expect(consoleErrorSpy).not.toHaveBeenCalled();
    }
  );

  it('сеть отвалилась → памятка ЖИВА (спросим на следующем входе), корзина цела', async () => {
    withCartAndPending();
    getMock.mockRejectedValue(new Error('network down'));

    await act(async () => {
      renderHook(() => useClearCartOnPaidOrder());
    });

    expect(usePendingOrderStore.getState().pending).not.toBeNull();
    expect(useCartStore.getState().items).toHaveLength(1);
  });

  // Отказавшая сверка выглядит снаружи ровно как исходный баг — «корзина не очистилась». Молчащий
  // catch означал бы, что о поломке команда узнаёт из жалобы клиента, а не из логов.
  it('неожиданный сбой сверки попадает в консоль', async () => {
    withCartAndPending();
    getMock.mockRejectedValue(new Error('network down'));

    await act(async () => {
      renderHook(() => useClearCartOnPaidOrder());
    });

    expect(consoleErrorSpy).toHaveBeenCalledWith(
      'Pending order check failed:',
      expect.any(Error)
    );
  });

  // 401 достижим: initData живёт 24 часа — ровно столько же, сколько памятка. 408 — наш
  // собственный таймаут. Оба означают «не смогли спросить», а не «сервер вынес вердикт»: снять на
  // них памятку значит НИКОГДА не очистить корзину у человека, который заплатил.
  it.each([401, 408, 500, 503])(
    'HTTP %i (не смогли спросить) → памятка ЖИВА, корзина цела',
    async (status) => {
      withCartAndPending();
      getMock.mockRejectedValue(new ApiError('nope', status));

      await act(async () => {
        renderHook(() => useClearCartOnPaidOrder());
      });

      expect(usePendingOrderStore.getState().pending).not.toBeNull();
      expect(useCartStore.getState().items).toHaveLength(1);
    }
  );

  it('памятка старше суток → ни одного запроса, памятка снята', async () => {
    useCartStore.getState().addItem(product);
    usePendingOrderStore.setState({
      pending: { orderId: 42, startedAt: Date.now() - PENDING_ORDER_TTL_MS - 1000 },
    });

    await act(async () => {
      renderHook(() => useClearCartOnPaidOrder());
    });

    expect(getMock).not.toHaveBeenCalled();
    expect(usePendingOrderStore.getState().pending).toBeNull();
    expect(useCartStore.getState().items).toHaveLength(1);
  });

  it('возврат видимости запускает сверку, уход в фон — нет', async () => {
    withCartAndPending();
    getMock.mockResolvedValue({ payment_status: 'unpaid', status: 'pending' });

    await act(async () => {
      renderHook(() => useClearCartOnPaidOrder());
    });
    expect(getMock).toHaveBeenCalledTimes(1); // старт

    await act(async () => {
      fireVisibility('hidden');
    });
    expect(getMock).toHaveBeenCalledTimes(1); // ушёл в фон — не спрашиваем

    await act(async () => {
      fireVisibility('visible');
    });
    expect(getMock).toHaveBeenCalledTimes(2); // вернулся — спрашиваем
  });

  it('телеграмное событие activated запускает сверку', async () => {
    withCartAndPending();
    getMock.mockResolvedValue({ payment_status: 'unpaid', status: 'pending' });

    await act(async () => {
      renderHook(() => useClearCartOnPaidOrder());
    });
    expect(getMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      activatedHandler()();
    });

    expect(getMock).toHaveBeenCalledTimes(2);
  });

  it('размонтирование снимает ОБЕ подписки — накопления слушателей нет', async () => {
    withCartAndPending();
    getMock.mockResolvedValue({ payment_status: 'unpaid', status: 'pending' });

    let unmount = () => {};
    await act(async () => {
      unmount = renderHook(() => useClearCartOnPaidOrder()).unmount;
    });
    const handler = activatedHandler();
    getMock.mockClear();

    await act(async () => {
      unmount();
    });

    expect(offEventMock).toHaveBeenCalledWith('activated', handler);

    // И живой проверкой, а не только фактом вызова offEvent: после размонтирования события молчат.
    await act(async () => {
      fireVisibility('visible');
    });
    expect(getMock).not.toHaveBeenCalled();
  });

  it('памятки нет → ни одного запроса (у тех, кто ничего не покупал)', async () => {
    useCartStore.getState().addItem(product);

    await act(async () => {
      renderHook(() => useClearCartOnPaidOrder());
    });
    await act(async () => {
      fireVisibility('visible');
    });

    expect(getMock).not.toHaveBeenCalled();
    expect(useCartStore.getState().items).toHaveLength(1);
  });

  // Хук исполняется у КАЖДОГО открывшего мини-апп: его отказ не имеет права стать белым экраном.
  it('неожиданное тело ответа → приложение не падает, памятка жива, корзина цела', async () => {
    withCartAndPending();
    getMock.mockResolvedValue(null);

    await act(async () => {
      renderHook(() => useClearCartOnPaidOrder());
    });

    expect(usePendingOrderStore.getState().pending).not.toBeNull();
    expect(useCartStore.getState().items).toHaveLength(1);
  });
});
