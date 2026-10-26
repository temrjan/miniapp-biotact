import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { useLanguageStore } from '@/stores/languageStore';
import { HomePage } from './HomePage';
import { CatalogPage } from './CatalogPage';
import { RamadanPage } from './RamadanPage';
import { ProductPage } from './ProductPage';
import { SalesPage } from './SalesPage';

function product(id: number, category = 101) {
  return { id, name: `Product ${id}`, slug: `product-${id}`, description: null,
    price: '100.25', old_price: null, is_sale: false, sale_price: null,
    image_url: 'https://app.biotact.uz/uploads/product.png', images: null,
    category_id: category, stock_quantity: 5, is_in_stock: true, is_purchasable: true,
    requires_size: false, available_sizes: [], additional_info_title: null,
    additional_info: null, disclaimer: null, video_url: null };
}
const categories = Array.from({ length: 102 }, (_, i) => ({ id: i + 1,
  name: `Category ${i + 1}`, slug: i === 100 ? 'product' : i === 101 ? 'ramadan-set' : `cat-${i + 1}`,
  image_url: null }));
let products = Array.from({ length: 111 }, (_, i) => product(i + 1, i < 100 ? 1 : 101));
const calls: URL[] = [];
afterEach(() => vi.unstubAllGlobals());

function page<T>(items: T[], url: URL, defaultSize: number) {
  const size = Number(url.searchParams.get('size') || defaultSize);
  const number = Number(url.searchParams.get('page') || 1);
  return { items: items.slice((number - 1) * size, number * size), total: items.length,
    page: number, size, pages: Math.max(1, Math.ceil(items.length / size)) };
}
function payload(url: URL) {
  if (url.pathname.endsWith('/categories')) return page(categories, url, 20);
  if (url.pathname.endsWith('/banners')) {
    const all = Array.from({ length: 102 }, (_, i) => ({ id: i + 1, title: `Banner ${i + 1}`,
      title_uz: null, subtitle: null, subtitle_uz: null, image_url: `/banner-${i + 1}.png`,
      gradient: null, link_url: null, sort_order: i, is_active: i >= 100 }));
    const items = url.searchParams.get('is_active') === 'true' ? all.filter(b => b.is_active) : all;
    const skip = Number(url.searchParams.get('skip') || 0);
    return { items: items.slice(skip, skip + Number(url.searchParams.get('limit') || 100)), total: items.length };
  }
  if (url.pathname.endsWith('/settings/banner_interval')) return { value: '3000' };
  if (url.pathname.endsWith('/products/101')) return product(101);
  if (url.pathname.endsWith('/products')) {
    const category = url.searchParams.get('category_id');
    const matching = url.searchParams.has('on_sale') ? [] : category ? products.filter(p => p.category_id === Number(category)) : products;
    return page(matching, url, 20);
  }
  throw new Error(`Unexpected URL ${url}`);
}
beforeEach(() => {
  calls.length = 0;
  products = Array.from({ length: 111 }, (_, i) => product(i + 1, i < 100 ? 1 : 101));
  useLanguageStore.setState({ language: 'ru' });
  vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
    const url = new URL(String(input), 'http://catalogue.test');
    calls.push(url);
    return new Response(JSON.stringify(payload(url)), { status: 200 });
  }));
});
function Location() { return <output data-testid="location">{useLocation().pathname}</output>; }
function mount(element: React.ReactNode, route = '/') {
  return render(<MemoryRouter initialEntries={[route]}><Location /><Routes>
    <Route path="/" element={element} />
    <Route path="/product/:productId" element={element} />
    <Route path="/catalog/:slug" element={<CatalogPage />} />
  </Routes></MemoryRouter>);
}

it('loads Home sections beyond both first pages, keeps eight previews and opens the slug route', async () => {
  mount(<HomePage />);
  expect(await screen.findByText('Product 101')).toBeInTheDocument();
  expect(screen.getByText('Product 108')).toBeInTheDocument();
  expect(screen.queryByText('Product 109')).not.toBeInTheDocument();
  fireEvent.click(screen.getAllByRole('button', { name: /Смотреть все/ })[0]);
  expect(await screen.findByTestId('location')).toHaveTextContent('/catalog/product');
  expect(await screen.findByText('Product 111')).toBeInTheDocument();
  expect(screen.queryByText('Product 1')).not.toBeInTheDocument();
  expect(calls.some(u => u.pathname.endsWith('/banners') && u.searchParams.get('is_active') === 'true')).toBe(true);
});

it('Catalogue Show more reaches the complete set after the first 100', async () => {
  mount(<CatalogPage />);
  await screen.findByText('Product 1');
  while (screen.queryByText(/Показать ещё/)) {
    fireEvent.click(screen.getByText(/Показать ещё/));
  }
  expect(screen.getByText('Product 111')).toBeInTheDocument();
});

it('Ramadan finds its later category and reads beyond the first 50', async () => {
  products = Array.from({ length: 52 }, (_, i) => product(i + 1, 102));
  mount(<RamadanPage />);
  expect(await screen.findByText('Product 52')).toBeInTheDocument();
});

it('detail sends canonical related parameters and shows five same-category mapped products', async () => {
  mount(<ProductPage />, '/product/101');
  await screen.findByRole('heading', { name: 'Product 101' });
  await waitFor(() => expect(calls.some(u => u.searchParams.get('category_id') === '101' && u.searchParams.get('size') === '10')).toBe(true));
  expect(await screen.findByText('Product 106')).toBeInTheDocument();
  expect(screen.queryByText('Product 1')).not.toBeInTheDocument();
  expect(screen.queryByText('Product 107')).not.toBeInTheDocument();
});

it('Home View all uses the existing category slug route even when the section was on page one', async () => {
  products = [product(101, 1)];
  const fetch = vi.mocked(globalThis.fetch);
  fetch.mockImplementation(async (input) => {
    const url = new URL(String(input), 'http://catalogue.test');
    calls.push(url);
    return new Response(JSON.stringify(url.pathname.endsWith('/categories')
      ? page([{ ...categories[100], id: 1 }], url, 20) : payload(url)));
  });
  mount(<HomePage />);
  await screen.findByText('Product 101');
  fireEvent.click(screen.getAllByRole('button', { name: /Смотреть все/ })[0]);
  expect(screen.getByTestId('location')).toHaveTextContent('/catalog/product');
});

it('old language results cannot replace current Home data', async () => {
  let resolveOld: ((r: Response) => void) | undefined;
  vi.mocked(globalThis.fetch).mockImplementation(async (input, config) => {
    const url = new URL(String(input), 'http://catalogue.test');
    const language = new Headers(config?.headers).get('Accept-Language');
    if (url.pathname.endsWith('/products') && !url.searchParams.has('on_sale') && language === 'ru')
      return new Promise<Response>(resolve => { resolveOld = resolve; });
    const body = url.pathname.endsWith('/products') && !url.searchParams.has('on_sale')
      ? page([{ ...product(101, 101), name: 'UZ current' }], url, 100) : payload(url);
    return new Response(JSON.stringify(body));
  });
  mount(<HomePage />);
  await waitFor(() => expect(resolveOld).toBeDefined());
  act(() => useLanguageStore.setState({ language: 'uz' }));
  expect(await screen.findByText('UZ current')).toBeInTheDocument();
  await act(async () => resolveOld?.(new Response(JSON.stringify(page([product(101)], new URL('http://test'), 100)))));
  expect(screen.queryByText('Product 101')).not.toBeInTheDocument();
});

it('a valid uncategorized detail makes no related request and keeps the usable detail', async () => {
  vi.mocked(globalThis.fetch).mockImplementation(async input => {
    const url = new URL(String(input), 'http://test');
    calls.push(url);
    return new Response(JSON.stringify({ ...product(101), category_id: null }));
  });
  mount(<ProductPage />, '/product/101');
  expect(await screen.findByRole('heading', { name: 'Product 101' })).toBeInTheDocument();
  expect(calls).toHaveLength(1);
  expect(screen.queryByText('Похожие товары')).not.toBeInTheDocument();
});

it('a fresh successful empty Home load removes the previous banners', async () => {
  const view = mount(<HomePage />);
  await waitFor(() => expect(view.container.querySelector('img[src="/banner-101.png"]')).toBeInTheDocument());
  vi.mocked(globalThis.fetch).mockImplementation(async input => {
    const url = new URL(String(input), 'http://test');
    return new Response(JSON.stringify(url.pathname.endsWith('/banners') ? { items: [], total: 0 } : payload(url)));
  });
  act(() => useLanguageStore.setState({ language: 'uz' }));
  await screen.findByText('Biotact Deutschland GmbH');
  expect(view.container.querySelector('img[src="/banner-101.png"]')).not.toBeInTheDocument();
});

it('Sales renders a later sale and Catalogue ignores an obsolete sale-filter result', async () => {
  products = Array.from({ length: 101 }, (_, i) => ({ ...product(i + 1, 1), is_sale: true, sale_price: '80.50' }));
  let old: ((response: Response) => void) | undefined;
  vi.mocked(globalThis.fetch).mockImplementation(async input => {
    const url = new URL(String(input), 'http://test');
    if (url.pathname.endsWith('/products') && !url.searchParams.has('on_sale'))
      return new Promise<Response>(resolve => { old = resolve; });
    return new Response(JSON.stringify(url.pathname.endsWith('/products') ? page(products, url, 100) : payload(url)));
  });
  const view = render(<MemoryRouter><CatalogPage /></MemoryRouter>);
  await waitFor(() => expect(old).toBeDefined());
  view.rerender(<MemoryRouter><CatalogPage onSale /></MemoryRouter>);
  await screen.findByText('Product 1');
  while (screen.queryByText(/Показать ещё/)) fireEvent.click(screen.getByText(/Показать ещё/));
  expect(screen.getByText('Product 101')).toBeInTheDocument();
  await act(async () => old?.(new Response(JSON.stringify(page([{ ...product(500), name: 'Old filter' }], new URL('http://test'), 100)))));
  expect(screen.queryByText('Old filter')).not.toBeInTheDocument();
  view.unmount();
  mount(<SalesPage />);
  await screen.findByText('Product 1');
  expect(screen.getByRole('heading', { name: /Акции/ })).toBeInTheDocument();
});
