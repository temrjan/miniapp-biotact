import { afterEach, expect, it, vi } from 'vitest';
import { loadActiveBanners, loadCategories, loadProducts } from './catalogue';
import { useLanguageStore } from '@/stores/languageStore';

afterEach(() => vi.unstubAllGlobals());
const category = (id: number) => ({ id, name: `C${id}`, slug: `c-${id}`, image_url: null });
function envelope(items: unknown[], total: number, page: number, size = 100) {
  return { items, total, page, size, pages: Math.max(1, Math.ceil(total / size)) };
}
it.each(['http', 'metadata', 'item'])('rejects a required failed/malformed second page (%s)', async failure => {
  const fetch = vi.fn(async (input: string | URL | Request) => {
    const url = new URL(String(input), 'http://test');
    if (url.searchParams.get('page') === '1')
      return new Response(JSON.stringify(envelope(Array.from({ length: 100 }, (_, i) => category(i + 1)), 101, 1)));
    if (failure === 'http') return new Response('{}', { status: 500 });
    return new Response(JSON.stringify(failure === 'metadata' ? { items: [category(101)] } : envelope([{ id: 101 }], 101, 2)));
  });
  vi.stubGlobal('fetch', fetch);
  await expect(loadCategories('ru')).rejects.toThrow();
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('terminates exact-full and empty traversals from metadata and starts a fresh list each time', async () => {
  let total = 100;
  const fetch = vi.fn(async () => new Response(JSON.stringify(envelope(Array.from({ length: total }, (_, i) => category(i + 1)), total, 1))));
  vi.stubGlobal('fetch', fetch);
  expect(await loadCategories('ru')).toHaveLength(100);
  total = 0;
  expect(await loadCategories('ru')).toEqual([]);
  expect(fetch).toHaveBeenCalledTimes(2);
});

it('keeps an explicit language and all product filters on every page while the store changes', async () => {
  const seen: { url: URL; language: string | null }[] = [];
  const product = (id: number) => ({ id, name: 'RU', slug: `p${id}`, price: '10.25', is_sale: true,
    stock_quantity: null, category_id: 7, is_in_stock: true, is_purchasable: false });
  vi.stubGlobal('fetch', vi.fn(async (input, options) => {
    const url = new URL(String(input), 'http://test');
    seen.push({ url, language: new Headers(options.headers).get('X-Language') });
    useLanguageStore.setState({ language: 'uz' });
    const page = Number(url.searchParams.get('page'));
    return new Response(JSON.stringify(envelope([product(page)], 2, page, 1)));
  }));
  expect(await loadProducts('ru', { category_id: '7', on_sale: 'true', include_all_translations: 'false' }, 1)).toHaveLength(2);
  for (const request of seen) {
    expect(request.language).toBe('ru');
    expect(Object.fromEntries(request.url.searchParams)).toMatchObject({ size: '1', category_id: '7', on_sale: 'true', include_all_translations: 'false' });
  }
});

it('reads every active banner and rejects a malformed later banner rather than a partial set', async () => {
  let malformed = false;
  vi.stubGlobal('fetch', vi.fn(async input => {
    const url = new URL(String(input), 'http://test');
    expect(url.searchParams.get('is_active')).toBe('true');
    const skip = Number(url.searchParams.get('skip'));
    const items = Array.from({ length: skip === 0 ? 100 : 1 }, (_, i) => ({
      id: skip + i + 1, title: 'Banner', sort_order: 0, is_active: true,
    }));
    return new Response(JSON.stringify({ items: malformed && skip ? [{}] : items, total: 101 }));
  }));
  expect(await loadActiveBanners('ru')).toHaveLength(101);
  malformed = true;
  await expect(loadActiveBanners('ru')).rejects.toThrow();
});
