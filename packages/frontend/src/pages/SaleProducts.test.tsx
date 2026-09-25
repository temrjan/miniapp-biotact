import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { api } from '@/api/client';
import { useCartStore } from '@/stores/cartStore';
import { useLanguageStore } from '@/stores/languageStore';
import { HomePage } from './HomePage';
import { App } from '@/App';
import { SalesPage } from './SalesPage';
import { CatalogPage } from './CatalogPage';
import { ProductPage } from './ProductPage';
import { ProductBottomSheet } from '@/components/features/ProductBottomSheet';

vi.mock('@/api/client', async (original) => {
  const actual = await original<typeof import('@/api/client')>();
  return { ...actual, api: { ...actual.api, get: vi.fn() } };
});

function apiProduct(isSale = true) {
  return {
    id: 1,
    name: 'Sale product',
    name_uz: null,
    slug: 'sale-product',
    description: '',
    price: 10000,
    old_price: 12000,
    is_sale: isSale,
    sale_price: '8000.50',
    image_url: '/product.png',
    category_id: 1,
    stock_quantity: 5,
    is_in_stock: true,
    is_purchasable: true,
  };
}
let current = apiProduct();

beforeEach(() => {
  current = apiProduct();
  useCartStore.setState({ items: [] });
  useLanguageStore.setState({ language: 'ru' });
  vi.mocked(api.get)
    .mockReset()
    .mockImplementation(async (url) => {
      if (url === '/products/1') return current;
      if (url.includes('on_sale=true'))
        return { items: current.is_sale ? [current] : [], total: current.is_sale ? 1 : 0 };
      if (url.startsWith('/products'))
        return { items: [current], total: 1, page: 1, size: 100, pages: 1 };
      if (url === '/categories') return { items: [{ id: 1, name: 'Products', slug: 'product' }] };
      if (url === '/banners') return { items: [] };
      if (url === '/settings/banner_interval') return { value: '3000' };
      throw new Error(`Unexpected GET ${url}`);
    });
});

const views = [
  { name: 'home', element: <HomePage /> },
  { name: 'catalog', element: <CatalogPage /> },
  { name: 'product', element: <ProductPage /> },
  { name: 'bottom sheet', element: <ProductBottomSheet productId="1" isOpen onClose={() => {}} /> },
];

function mount(element: React.ReactNode) {
  return render(
    <MemoryRouter initialEntries={['/product/1']}>
      <Routes>
        <Route path="/product/:productId" element={element} />
      </Routes>
    </MemoryRouter>
  );
}

for (const view of views) {
  describe(view.name, () => {
    it('should show the effective price, original price and badge when on sale', async () => {
      mount(view.element);
      expect((await screen.findAllByText('8 000,5 сум'))[0]).toHaveClass('text-[var(--primary)]');
      expect(screen.getAllByText('10 000 сум')[0]).toHaveClass('line-through');
      expect(screen.getAllByText('Акция').length).toBeGreaterThan(0);
      expect(screen.queryByText('12 000 сум')).not.toBeInTheDocument();
      fireEvent.click(screen.getAllByText('В корзину')[0]);
      await waitFor(() =>
        expect(useCartStore.getState().items[0]?.product).toMatchObject({
          price: 10000,
          isSale: true,
          salePrice: 8000.5,
        })
      );
    });

    it('should retain the old price without a sale badge when sale is disabled', async () => {
      current = apiProduct(false);
      mount(view.element);
      expect((await screen.findAllByText('10 000 сум'))[0]).toBeInTheDocument();
      expect(screen.getAllByText('12 000 сум')[0]).toHaveClass('line-through');
      expect(screen.queryByText('Акция')).not.toBeInTheDocument();
      expect(screen.queryByText('8 000,5 сум')).not.toBeInTheDocument();
    });

    it('should translate the sale badge when Uzbek is selected', async () => {
      useLanguageStore.setState({ language: 'uz' });
      mount(view.element);
      expect((await screen.findAllByText('Aksiya'))[0]).toBeInTheDocument();
    });
  });
}

describe('sales navigation', () => {
  it('should open only sale products when following All sales from home', async () => {
    const network = vi.mocked(api.get).getMockImplementation();
    if (!network) throw new Error('Missing network fixture');
    vi.mocked(api.get).mockImplementation(async (url) => {
      if (url === '/products?size=100')
        return { items: [{ ...apiProduct(false), id: 2, name: 'Ordinary product' }] };
      return network(url);
    });
    render(
      <MemoryRouter>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/sales" element={<SalesPage />} />
        </Routes>
      </MemoryRouter>
    );
    const link = await screen.findByRole('link', { name: 'Все акции' });
    expect(link).toHaveAttribute('href', '/sales');
    expect(screen.getByText('Ordinary product')).toBeInTheDocument();
    fireEvent.click(link);
    expect(await screen.findByRole('heading', { name: /Акции/ })).toBeInTheDocument();
    expect(screen.getByText('Sale product')).toBeInTheDocument();
    expect(screen.queryByText('Ordinary product')).not.toBeInTheDocument();
    expect(screen.getByText('8 000,5 сум')).toBeInTheDocument();
  });
  it('should display the empty catalog state when there are no active sales', async () => {
    current = apiProduct(false);
    mount(<SalesPage />);
    expect(await screen.findByText('Товары не найдены')).toBeInTheDocument();
    expect(screen.queryByText('Sale product')).not.toBeInTheDocument();
  });
});

it('should register the sales route in the application', async () => {
  window.history.replaceState({}, '', '/sales');
  try {
    render(<App />);
    expect(await screen.findByRole('heading', { name: /Акции/ })).toBeInTheDocument();
    expect(screen.getByText('8 000,5 сум')).toBeInTheDocument();
  } finally {
    window.history.replaceState({}, '', '/');
  }
});

it('should use sale pricing for similar products when displaying a product', async () => {
  const network = vi.mocked(api.get).getMockImplementation();
  if (!network) throw new Error('Missing network fixture');
  vi.mocked(api.get).mockImplementation(async (url) => {
    if (url.startsWith('/products?'))
      return { items: [{ ...current, id: 2, name: 'Similar sale', sale_price: 7500 }] };
    return network(url);
  });
  mount(<ProductPage />);
  expect(await screen.findByText('Similar sale')).toBeInTheDocument();
  expect(screen.getByText('7 500 сум')).toHaveClass('text-[var(--primary)]');
  expect(screen.getAllByText('Акция')).toHaveLength(2);
  expect(screen.getAllByText('10 000 сум')).toHaveLength(2);
  for (const price of screen.getAllByText('10 000 сум')) expect(price).toHaveClass('line-through');
});

it('should retain ordinary home sections when the sales request fails', async () => {
  current = apiProduct(false);
  const network = vi.mocked(api.get).getMockImplementation();
  if (!network) throw new Error('Missing network fixture');
  vi.mocked(api.get).mockImplementation(async (url) => {
    if (url.includes('on_sale=true')) throw new Error('offline');
    return network(url);
  });
  mount(<HomePage />);
  expect(await screen.findByText('Sale product')).toBeInTheDocument();
  expect(screen.getByText('10 000 сум')).toBeInTheDocument();
  expect(screen.queryByRole('link', { name: 'Все акции' })).not.toBeInTheDocument();
});
