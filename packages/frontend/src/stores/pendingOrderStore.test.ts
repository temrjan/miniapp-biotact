import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

import { STORAGE_KEYS } from '@/utils/constants';
import {
  PENDING_ORDER_TTL_MS,
  isPendingOrderExpired,
  usePendingOrderStore,
} from './pendingOrderStore';

describe('pendingOrderStore', () => {
  beforeEach(() => {
    usePendingOrderStore.getState().forgetOrder();
    localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('стартует без памятки', () => {
    expect(usePendingOrderStore.getState().pending).toBeNull();
  });

  it('запоминает ЧИСЛОВОЙ id заказа и момент ухода на оплату', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-08-06T10:00:00Z'));

    usePendingOrderStore.getState().rememberOrder(42);

    expect(usePendingOrderStore.getState().pending).toEqual({
      orderId: 42,
      startedAt: new Date('2026-08-06T10:00:00Z').getTime(),
    });
  });

  it('снимает памятку', () => {
    usePendingOrderStore.getState().rememberOrder(42);
    usePendingOrderStore.getState().forgetOrder();

    expect(usePendingOrderStore.getState().pending).toBeNull();
  });

  // Обосновывающий случай фикса — «закрыл браузер, вернулся через час» — тот, где мини-апп был
  // выгружен. Памятка обязана лежать в localStorage, иначе проверка на старте нечего не найдёт.
  it('переживает перезапуск приложения — лежит в localStorage под своим ключом', () => {
    usePendingOrderStore.getState().rememberOrder(42);

    const raw = localStorage.getItem(STORAGE_KEYS.PENDING_ORDER);
    expect(raw).not.toBeNull();
    expect(JSON.parse(raw as string)).toMatchObject({ state: { pending: { orderId: 42 } } });
  });

  describe('isPendingOrderExpired', () => {
    const startedAt = new Date('2026-08-06T10:00:00Z').getTime();

    it('свежая памятка не просрочена', () => {
      expect(isPendingOrderExpired({ orderId: 1, startedAt }, startedAt + 60_000)).toBe(false);
    });

    it('ровно на границе срока ещё не просрочена', () => {
      expect(
        isPendingOrderExpired({ orderId: 1, startedAt }, startedAt + PENDING_ORDER_TTL_MS)
      ).toBe(false);
    });

    it('на миллисекунду позже границы — просрочена', () => {
      expect(
        isPendingOrderExpired({ orderId: 1, startedAt }, startedAt + PENDING_ORDER_TTL_MS + 1)
      ).toBe(true);
    });
  });
});
