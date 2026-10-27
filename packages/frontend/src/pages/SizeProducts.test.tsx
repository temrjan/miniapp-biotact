import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { api } from '@/api/client';
import { useCartStore } from '@/stores/cartStore';
import { useLanguageStore } from '@/stores/languageStore';
import { HomePage } from './HomePage';
import { CatalogPage } from './CatalogPage';
import { RamadanPage } from './RamadanPage';
import { ProductPage } from './ProductPage';
import { ProductBottomSheet } from '@/components/features/ProductBottomSheet';

vi.mock('@/api/client', async (original) => {
  const actual = await original<typeof import('@/api/client')>();
  return { ...actual, api: { ...actual.api, get: vi.fn() } };
});
const makeVest = () => ({
  id: 39,
  name: 'Vest',
  slug: 'vest',
  description: '',
  price: '30000.00',
  old_price: null,
  is_sale: false,
  sale_price: null,
  image_url: '/vest.png',
  category_id: 1,
  stock_quantity: null,
  is_active: true,
  is_in_stock: true,
  is_purchasable: true,
  requires_size: true,
  available_sizes: ['L', 'XL', '2XL'],
});
let vest = makeVest();
beforeEach(() => {
  vest = makeVest();
  useCartStore.getState().clearCart();
  useLanguageStore.setState({ language: 'ru' });
  vi.mocked(api.get)
    .mockReset()
    .mockImplementation(async (url) => {
      const query = new URL(url, 'http://test').searchParams;
      const envelope = (items: unknown[]) => ({ items, total: items.length, page: 1, size: Number(query.get('size') || 20), pages: 1 });
      if (url === '/products/39') return vest;
      if (url.startsWith('/products')) return envelope([vest]);
      if (url.startsWith('/categories'))
        return envelope([
            { id: 1, name: 'Products', slug: 'product' },
            { id: 2, name: 'Ramadan', slug: 'ramadan-set' },
          ]);
      if (url.startsWith('/banners')) return { items: [], total: 0 };
      if (url === '/settings/banner_interval') return { value: '3000' };
      throw new Error(`Unexpected GET ${url}`);
    });
});
function mount(element: React.ReactNode) {
  return render(
    <MemoryRouter initialEntries={['/entry']}>
      <Routes>
        <Route path="/entry" element={element} />
        <Route path="/product/:productId" element={<ProductPage />} />
      </Routes>
    </MemoryRouter>
  );
}

for (const [name, element] of [
  ['home', <HomePage />],
  ['catalog', <CatalogPage />],
  ['Ramadan', <RamadanPage />],
] as const) {
  it(`${name} direct add opens selection without adding a size-less item`, async () => {
    mount(element);
    fireEvent.click((await screen.findAllByRole('button', { name: 'В корзину' }))[0]);
    expect(await screen.findByRole('button', { name: 'XL', exact: true })).toBeInTheDocument();
    expect(useCartStore.getState().items).toEqual([]);
  });
  it(`${name} keeps a depleted card visible and disables purchase`, async () => {
    vest.available_sizes = [];
    vest.is_in_stock = false;
    mount(element);
    expect((await screen.findAllByText('Vest')).length).toBeGreaterThan(0);
    const buttons = await screen.findAllByRole('button', { name: 'Нет в наличии' });
    for (const button of buttons) expect(button).toBeDisabled();
  });
}

for (const language of ['ru', 'uz'] as const) {
  for (const [name, element] of [
    ['full page', <ProductPage />],
    ['bottom sheet', <ProductBottomSheet productId="39" isOpen onClose={() => {}} />],
  ] as const) {
    it(`${name} requires explicit selection in ${language} and hides depleted choices`, async () => {
      vest.available_sizes = ['L', '2XL'];
      useLanguageStore.setState({ language });
      render(
        <MemoryRouter initialEntries={['/product/39']}>
          <Routes>
            <Route path="/product/:productId" element={element} />
          </Routes>
        </MemoryRouter>
      );
      const label = language === 'ru' ? 'В корзину' : 'Savatga';
      const add = await screen.findByRole('button', { name: new RegExp(label) });
      expect(add).toBeDisabled();
      expect(screen.queryByRole('button', { name: 'XL', exact: true })).not.toBeInTheDocument();
      const size = screen.getByRole('button', { name: '2XL', exact: true });
      expect(size).toHaveAttribute('aria-pressed', 'false');
      fireEvent.click(size);
      await waitFor(() => expect(add).toBeEnabled());
      fireEvent.click(add);
      expect(useCartStore.getState().items[0]).toMatchObject({
        productId: '39',
        size: '2XL',
        quantity: 1,
      });
    });
  }
}

it('full page video sends the origin as referrer despite the document no-referrer policy', async () => {
  Object.assign(vest, { video_url: 'https://www.youtube.com/watch?v=abc123XYZ' });
  render(
    <MemoryRouter initialEntries={['/product/39']}>
      <Routes>
        <Route path="/product/:productId" element={<ProductPage />} />
      </Routes>
    </MemoryRouter>
  );
  const video = await screen.findByTitle('Product video');
  expect(video).toHaveAttribute('src', 'https://www.youtube.com/embed/abc123XYZ');
  expect(video).toHaveAttribute('referrerpolicy', 'strict-origin-when-cross-origin');
});
