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

it.each([
  'image_mobile_url',
  'video_url',
  'video_mobile_url',
] as const)('D-03 / M-02: validates %s scheme, credentials, relative rules and non-string safety without dropping the banner', async (field) => {
  const base = {
    id: 10,
    title: 'Banner',
    title_uz: 'Banner UZ',
    subtitle: 'Sub',
    subtitle_uz: 'Sub UZ',
    image_url: 'https://cdn.example.test/wide.jpg',
    gradient: null,
    link_url: '/product/1',
    sort_order: 0,
    is_active: true,
  };
  const validHttps = `https://cdn.example.test/${field}`;
  const pathAtHandle = `https://cdn.example.test/@biotact/${field}`;
  const relative = '/uploads/banners/mobile.jpg';
  const payloads = [
    base,
    { ...base, id: 11, [field]: validHttps },
    { ...base, id: 12, [field]: pathAtHandle },
    { ...base, id: 13, [field]: `http://cdn.example.test/${field}` },
    { ...base, id: 14, [field]: `https://user:pass@cdn.example.test/${field}` },
    { ...base, id: 15, [field]: `https://user@cdn.example.test/${field}` },
    { ...base, id: 16, [field]: `https://@cdn.example.test/${field}` },
    { ...base, id: 17, [field]: 'javascript:alert(1)' },
    { ...base, id: 18, [field]: relative },
    { ...base, id: 19, [field]: '//evil.example/media' },
    { ...base, id: 20, [field]: '/\\evil/media' },
    { ...base, id: 21, [field]: 12345 },
    { ...base, id: 22, [field]: { url: validHttps } },
  ];
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify({ items: payloads, total: payloads.length })))
  );

  const banners = await loadActiveBanners('ru');
  expect(banners).toHaveLength(payloads.length);
  expect(banners[0]?.[field]).toBeNull();
  expect(banners[1]?.[field]).toBe(validHttps);
  expect(banners[2]?.[field]).toBe(pathAtHandle);
  for (const rejectedIdx of [3, 4, 5, 6, 7, 9, 10, 11, 12]) {
    expect(banners[rejectedIdx]?.[field]).toBeNull();
  }
  expect(banners[8]?.[field]).toBe(field === 'image_mobile_url' ? relative : null);
});
