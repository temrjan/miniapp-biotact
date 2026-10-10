import { act, renderHook } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import type { Product } from '@/types';
import { needsSizeChoice } from '@/utils/size';
import { buildOrderRequest } from '@/utils/order';
import { useCartStore, useRefreshCartPrices } from './cartStore';

vi.mock('@/api/client', () => ({ api: { get: vi.fn() } }));
const vest: Product = {
  id: '39',
  name: 'Vest',
  description: '',
  price: 30000,
  imageUrl: '',
  categoryId: '1',
  inStock: true,
  requiresSize: true,
  availableSizes: ['L', 'XL', '2XL'],
};
beforeEach(() => {
  useCartStore.getState().clearCart();
  vi.mocked(api.get).mockReset();
});

it('requires an explicit available size and keeps size identities independent', () => {
  const cart = useCartStore.getState();
  cart.addItem(vest);
  cart.addItem(vest, 1, 'XXL');
  expect(useCartStore.getState().items).toEqual([]);
  cart.addItem(vest, 2, 'L');
  cart.addItem(vest, 1, 'XL');
  cart.addItem(vest, 1, 'L');
  expect(useCartStore.getState().items.map(({ size, quantity }) => [size, quantity])).toEqual([
    ['L', 3],
    ['XL', 1],
  ]);
  cart.updateQuantity('39', 7, 'XL');
  cart.removeItem('39', 'L');
  expect(
    buildOrderRequest(useCartStore.getState().items, {
      deliveryAddress: null,
      name: null,
      phone: null,
    }).items
  ).toEqual([{ product_id: 39, quantity: 7, size: 'XL' }]);
});

it('preserves requested units when correcting a restored size-less cart', () => {
  useCartStore.setState({ items: [{ productId: '39', product: vest, quantity: 4 }] });
  expect(needsSizeChoice(useCartStore.getState().items[0])).toBe(true);
  useCartStore.getState().chooseSize('39', null, '2XL');
  expect(useCartStore.getState().items[0]).toMatchObject({ size: '2XL', quantity: 4 });
});

it('a delayed availability refresh keeps an edit and removes cached legacy inventory counts', async () => {
  useCartStore.setState({
    items: [
      { productId: '39', product: { ...vest, requiresSize: false, quantity: 50 }, quantity: 4 },
    ],
  });
  let resolve: ((value: unknown) => void) | undefined;
  vi.mocked(api.get).mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done;
      })
  );
  renderHook(() => useRefreshCartPrices());
  act(() => useCartStore.getState().updateQuantity('39', 7));
  await act(async () => {
    if (!resolve) throw new Error('Refresh did not request the product');
    resolve({
      price: '30000.00',
      requires_size: true,
      available_sizes: ['XL'],
      is_in_stock: true,
      is_purchasable: true,
      stock_quantity: null,
    });
  });
  const line = useCartStore.getState().items[0];
  expect(line.quantity).toBe(7);
  expect(line.product.requiresSize).toBe(true);
  expect(line.product.quantity).toBeUndefined();
  expect(needsSizeChoice(line)).toBe(true);
  expect(JSON.stringify(line)).not.toContain('available_quantity');
});

it('does not admit a depleted, inactive, or nonpurchasable sized product', () => {
  for (const product of [
    { ...vest, availableSizes: [] },
    { ...vest, inStock: false },
    { ...vest, isActive: false },
    { ...vest, isPurchasable: false },
  ]) {
    useCartStore.getState().addItem(product, 1, 'L');
  }
  expect(useCartStore.getState().items).toEqual([]);
});
