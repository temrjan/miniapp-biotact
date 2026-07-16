import type { ReactNode } from 'react';
import { createElement } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import WebApp from '@twa-dev/sdk';

import { api, ApiError } from '@/api/client';
import type { CartItem, Product } from '@/types';
import { usePayment } from './usePayment';

// useNavigate → spy (hoisted so the vi.mock factory can reference it).
const { navigateMock } = vi.hoisted(() => ({ navigateMock: vi.fn() }));
vi.mock('react-router-dom', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react-router-dom')>();
  return { ...actual, useNavigate: () => navigateMock };
});

// api client → post() spy + an ApiError that carries status + data (like the real one).
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
  return { ApiError, api: { post: vi.fn(), get: vi.fn() } };
});

const postMock = vi.mocked(api.post);
const openLinkMock = vi.mocked(WebApp.openLink);

const product = { id: '7', name: 'X', price: 30000 } as Product;
const items: CartItem[] = [{ productId: '7', product, quantity: 1 }];

function wrapper({ children }: { children: ReactNode }) {
  return createElement(MemoryRouter, null, children);
}

type Options = Parameters<typeof usePayment>[0];
function renderPay(overrides: Partial<Options> = {}) {
  const onInvalidAmount = vi.fn();
  const { result } = renderHook(
    () =>
      usePayment({
        items,
        deliveryAddress: 'Tashkent',
        name: 'Тест',
        phone: '+998900000000',
        redeemPoints: 5000,
        onInvalidAmount,
        ...overrides,
      }),
    { wrapper }
  );
  return { result, onInvalidAmount };
}

const callsTo = (endpoint: string) => postMock.mock.calls.filter((c) => c[0] === endpoint);
const bodyOf = (endpoint: string) => callsTo(endpoint)[0]?.[1] as Record<string, unknown> | undefined;

beforeEach(() => {
  postMock.mockReset();
  navigateMock.mockReset();
  openLinkMock.mockReset();
  // jsdom does not implement window.alert — stub it so error paths don't blow up the test.
  vi.spyOn(window, 'alert').mockImplementation(() => {});
});

describe('usePayment', () => {
  it('двойной СИНХРОННЫЙ вызов pay → POST /orders ровно ОДИН раз (ref-гвард)', async () => {
    // Прямой вызов хендлера дважды подряд, не клик по DOM-кнопке: единственный способ доказать, что
    // защищает синхронный ref, а не побочный disable виджета (нативный MainButton дизейблится через
    // мост с задержкой — DOM-клик покрыл бы только web-кнопку).
    postMock.mockImplementation((endpoint: string) =>
      endpoint === '/orders'
        ? Promise.resolve({
            id: 1,
            order_number: 'ORD-1',
            total: 30000,
            payment_status: 'unpaid',
            points_applied: 5000,
          })
        : Promise.resolve({ payment_url: 'https://pay/1', payment_uuid: 'u', order_id: 1 })
    );

    const { result } = renderPay();
    await act(async () => {
      const p1 = result.current.pay();
      const p2 = result.current.pay(); // синхронно — до того как первый дойдёт до await
      await Promise.all([p1, p2]);
    });

    expect(callsTo('/orders')).toHaveLength(1);
  });

  it('полное покрытие (paid) → navigate на success, БЕЗ /payments/create', async () => {
    postMock.mockResolvedValue({
      id: 9,
      order_number: 'ORD-9',
      total: 30000,
      payment_status: 'paid',
      points_applied: 30000,
    });

    const { result } = renderPay({ redeemPoints: 30000 });
    await act(async () => {
      await result.current.pay();
    });

    expect(callsTo('/orders')).toHaveLength(1);
    expect(callsTo('/payments/create')).toHaveLength(0);
    expect(navigateMock).toHaveBeenCalledWith('/payment/success?order_id=ORD-9');
  });

  it('частичное (unpaid) → /orders с points_to_apply, затем /payments/create + openLink', async () => {
    postMock.mockImplementation((endpoint: string) =>
      endpoint === '/orders'
        ? Promise.resolve({
            id: 5,
            order_number: 'ORD-5',
            total: 30000,
            payment_status: 'unpaid',
            points_applied: 5000,
          })
        : Promise.resolve({ payment_url: 'https://pay/5', payment_uuid: 'u', order_id: 5 })
    );

    const { result } = renderPay({ redeemPoints: 5000 });
    await act(async () => {
      await result.current.pay();
    });

    expect(bodyOf('/orders')).toMatchObject({ points_to_apply: 5000 });
    expect(bodyOf('/payments/create')).toMatchObject({ order_id: 5 });
    expect(openLinkMock).toHaveBeenCalledWith('https://pay/5');
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it('422 → onInvalidAmount с max_redeemable из тела сервера', async () => {
    postMock.mockRejectedValue(
      new ApiError('unprocessable', 422, {
        detail: { error: 'invalid_redeem_amount', max_redeemable: 4000 },
      })
    );

    const { result, onInvalidAmount } = renderPay();
    await act(async () => {
      await result.current.pay();
    });

    expect(onInvalidAmount).toHaveBeenCalledWith(4000);
  });
});
