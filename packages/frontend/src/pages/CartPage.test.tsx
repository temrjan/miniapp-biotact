import { beforeEach, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { api } from '@/api/client';
import { useCartStore } from '@/stores/cartStore';
import { useLanguageStore } from '@/stores/languageStore';
import { STORAGE_KEYS } from '@/utils/constants';
import type { Product } from '@/types';
import { CartPage } from './CartPage';

vi.mock('@/api/client', async (original) => {
  const actual = await original<typeof import('@/api/client')>();
  return { ...actual, api: { ...actual.api, get: vi.fn() } };
});
const product: Product = {
  id: '1',
  name: 'Persisted product',
  description: '',
  price: 20000,
  imageUrl: '/product.png',
  categoryId: '1',
  inStock: true,
};
const fresh = { price: '10000', old_price: '12000', is_sale: true, sale_price: '8000.50' };
function mount() {
  return render(
    <MemoryRouter>
      <Routes>
        <Route path="/" element={<CartPage />} />
        <Route path="/checkout" element={<p>CHECKOUT</p>} />
      </Routes>
    </MemoryRouter>
  );
}
beforeEach(async () => {
  useLanguageStore.setState({ language: 'ru' });
  useCartStore.setState({ items: [] });
  localStorage.setItem(
    STORAGE_KEYS.CART,
    JSON.stringify({
      state: {
        items: [{ productId: '1', product, quantity: 2 }],
      },
      version: 0,
    })
  );
  await useCartStore.persist.rehydrate();
  vi.mocked(api.get).mockReset().mockResolvedValue(fresh);
});
it('should refresh the persisted price and total when opening the cart', async () => {
  mount();
  expect(await screen.findByText('8 000,5 сум')).toHaveClass('text-[var(--primary)]');
  expect(screen.getByText('10 000 сум')).toHaveClass('line-through');
  expect(screen.getByText('Акция')).toBeInTheDocument();
  expect(screen.getByText('16 001 сум')).toBeInTheDocument();
  expect(screen.queryByText('40 000 сум')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Оформить заказ' }));
  expect(screen.getByText('CHECKOUT')).toBeInTheDocument();
  expect(useCartStore.getState().items[0]).toMatchObject({
    quantity: 2,
    product: { price: 10000, salePrice: 8000.5 },
  });
});
it('should stop applying a persisted sale when the catalog disables it', async () => {
  useCartStore.setState({
    items: [
      { productId: '1', product: { ...product, isSale: true, salePrice: 5000 }, quantity: 2 },
    ],
  });
  vi.mocked(api.get).mockResolvedValue({ ...fresh, is_sale: false });
  mount();
  expect(await screen.findByText('10 000 сум')).toBeInTheDocument();
  expect(screen.getByText('20 000 сум')).toBeInTheDocument();
  expect(screen.queryByText('Акция')).not.toBeInTheDocument();
});
it('should preserve quantity changes when catalog loading finishes later', async () => {
  let resolve: ((value: typeof fresh) => void) | undefined;
  vi.mocked(api.get).mockImplementation(
    () =>
      new Promise((r) => {
        resolve = r;
      })
  );
  mount();
  act(() => useCartStore.getState().updateQuantity('1', 3));
  await act(async () => {
    if (!resolve) throw new Error('Catalog request was not started');
    resolve(fresh);
  });
  expect(await screen.findByText('24 001,5 сум')).toBeInTheDocument();
  expect(useCartStore.getState().items[0]?.quantity).toBe(3);
});
it('should not restore a removed item when its catalog response arrives', async () => {
  let resolve: ((value: typeof fresh) => void) | undefined;
  vi.mocked(api.get).mockImplementation(
    () =>
      new Promise((r) => {
        resolve = r;
      })
  );
  mount();
  act(() => useCartStore.getState().removeItem('1'));
  await act(async () => {
    if (!resolve) throw new Error('Catalog request was not started');
    resolve(fresh);
  });
  expect(screen.getByText('Корзина пуста')).toBeInTheDocument();
  expect(useCartStore.getState().items).toEqual([]);
});
it('should preserve the cart when catalog refresh fails', async () => {
  vi.mocked(api.get).mockRejectedValue(new Error('offline'));
  mount();
  await waitFor(() => expect(api.get).toHaveBeenCalled());
  await act(async () => {
    await Promise.resolve();
  });
  expect(screen.getByText('40 000 сум')).toBeInTheDocument();
  expect(useCartStore.getState().items).toEqual([{ productId: '1', product, quantity: 2 }]);
});
