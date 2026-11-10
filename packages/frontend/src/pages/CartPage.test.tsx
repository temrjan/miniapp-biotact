import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
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
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Routes>
          <Route path="/" element={<CartPage />} />
          <Route path="/checkout" element={<p>CHECKOUT</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
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
  vi.mocked(api.get).mockImplementation((url: string) =>
    url === '/products/1'
      ? new Promise((r) => {
          resolve = r;
        })
      : Promise.resolve({ active: false })
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
  vi.mocked(api.get).mockImplementation((url: string) =>
    url === '/products/1'
      ? new Promise((r) => {
          resolve = r;
        })
      : Promise.resolve({ active: false })
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

// event-qr-discount R-51: with an active grant the cart shows the R-10 discount and reduced total.
// Expected sums are worked out by hand from the R-10 rule (promotion unit price = regular price
// × 95 / 100, rounded half up to a whole sum; a line keeps its price when its sale price is at or
// below that), not by the code under test.
describe('R-51 event discount in the cart', () => {
  type Line = { id: string; price: number; sale: number | null; quantity: number };
  const grant = { active: true, title: 'NUTRIFORUM 2026', percent: 5, ends_at: '2026-12-31T19:00:00Z' };
  function seed(lines: Line[], promotion: object) {
    useCartStore.setState({
      items: lines.map((line) => ({
        productId: line.id,
        quantity: line.quantity,
        product: { ...product, id: line.id, name: `Product ${line.id}`, price: line.price,
          isSale: line.sale !== null, salePrice: line.sale },
      })),
    });
    vi.mocked(api.get).mockImplementation(async (url: string) => {
      if (url === '/event-promotions/me') return promotion;
      const line = lines.find((l) => url === `/products/${l.id}`);
      if (!line) throw new Error(`Unexpected GET ${url}`);
      return { price: String(line.price), old_price: null, is_sale: line.sale !== null,
        sale_price: line.sale === null ? null : String(line.sale) };
    });
  }
  const hint = 'На товары со своей скидкой дополнительная скидка не действует';

  it.each([
    ['regular product', [{ id: '1', price: 100000, sale: null, quantity: 1 }], '5 000', '95 000', false],
    ['regular product, quantity 2, half-up rounding', [{ id: '1', price: 10030, sale: null, quantity: 2 }], '1 002', '19 058', false],
    ['sale above the promotion price, quantity 3', [{ id: '1', price: 10000, sale: 9800, quantity: 3 }], '900', '28 500', false],
    ['regular product and a sale product at or below it',
      [{ id: '1', price: 100000, sale: null, quantity: 1 }, { id: '2', price: 10000, sale: 8000, quantity: 1 }],
      '5 000', '103 000', true],
  ] as const)('%s', async (_name, lines, discount, total, withHint) => {
    seed([...lines], grant);
    mount();
    expect(await screen.findByText(`Скидка гостя NUTRIFORUM 2026 5%: −${discount} сум`)).toBeInTheDocument();
    expect(screen.getByText(`${total} сум`, { selector: 'span.text-xl' })).toBeInTheDocument();
    if (withHint) expect(screen.getByText(hint)).toBeInTheDocument();
    else expect(screen.queryByText(hint)).not.toBeInTheDocument();
  });

  it('shows no discount line, only the hint, when every line keeps its own lower price', async () => {
    seed([{ id: '2', price: 10000, sale: 9500, quantity: 2 }], grant);
    mount();
    expect(await screen.findByText(hint)).toBeInTheDocument();
    expect(screen.queryByText(/Скидка гостя/)).not.toBeInTheDocument();
    expect(screen.getByText('19 000 сум', { selector: 'span.text-xl' })).toBeInTheDocument();
  });

  it('keeps the ordinary total without an active grant', async () => {
    seed([{ id: '1', price: 100000, sale: null, quantity: 1 }], { active: false });
    mount();
    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/event-promotions/me'));
    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.getByText('100 000 сум', { selector: 'span.text-xl' })).toBeInTheDocument();
    expect(screen.queryByText(/Скидка гостя/)).not.toBeInTheDocument();
    expect(screen.queryByText(hint)).not.toBeInTheDocument();
  });
});
