import { describe, it, expect } from 'vitest';

import type { CartItem, Product } from '@/types';
import { buildOrderRequest } from './order';

/**
 * The contract under test: the create-order request carries NO money. The server prices every line
 * from the catalogue (#79 — the checkout used to trust a client-declared price). Assertions go
 * against the object KEYS, not the values: that pins the intent ("money never travels from here"),
 * so re-adding a price field fails the test even if its value happens to be correct.
 */

const product = { id: '7', name: 'Кружка', price: 80000 } as Product;

const cart: CartItem[] = [
  { productId: '7', product, quantity: 2 },
  { productId: '11', product: { ...product, id: '11', price: 15000 } as Product, quantity: 1 },
];

const delivery = { deliveryAddress: 'Ташкент, Амира Темура 1', phone: '+998900000000', name: 'Тимур' };

describe('buildOrderRequest', () => {
  it('отправляет только товар и количество, product_id — числом', () => {
    const request = buildOrderRequest(cart, delivery);

    expect(request.items).toEqual([
      { product_id: 7, quantity: 2 },
      { product_id: 11, quantity: 1 },
    ]);
  });

  it('в теле заказа НЕТ денежных полей', () => {
    const request = buildOrderRequest(cart, delivery);

    const keys = Object.keys(request);
    expect(keys).not.toContain('subtotal');
    expect(keys).not.toContain('total');
    expect(keys).not.toContain('delivery_fee');
  });

  it('в позиции НЕТ цены и суммы', () => {
    const request = buildOrderRequest(cart, delivery);

    for (const line of request.items) {
      const keys = Object.keys(line);
      expect(keys).not.toContain('product_price');
      expect(keys).not.toContain('total');
      expect(keys).not.toContain('product_name');
      expect(keys.sort()).toEqual(['product_id', 'quantity']);
    }
  });

  it('самовывоз — адрес null; доставка — адрес строкой', () => {
    const pickup = buildOrderRequest(cart, { ...delivery, deliveryAddress: null });
    expect(pickup.delivery_address).toBeNull();

    const courier = buildOrderRequest(cart, delivery);
    expect(courier.delivery_address).toBe('Ташкент, Амира Темура 1');
  });

  it('пустая корзина — пустой список позиций (сервер ответит 422, не заказ на 0)', () => {
    expect(buildOrderRequest([], delivery).items).toEqual([]);
  });
});
