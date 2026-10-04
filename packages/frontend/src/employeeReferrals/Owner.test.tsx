import '@testing-library/jest-dom/vitest';
import { StrictMode } from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { ReferralOwner } from './Owner';
import { referralContext } from './runtime';
import { ProductPage } from '@/pages/ProductPage';
import { useCartStore } from '@/stores/cartStore';

const token = 'AAAAAAAAAAAAAAAAAAAAAA';
function deferred() {
  let finish: (response: Response) => void = () => {};
  const promise = new Promise<Response>((resolve) => {
    finish = resolve;
  });
  return { promise, finish };
}
function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
function product(id: number) {
  return {
    id,
    name: `Product ${id}`,
    name_uz: null,
    description: '',
    price: 10000,
    old_price: null,
    is_sale: false,
    sale_price: null,
    image_url: '/product.png',
    category_id: id,
    stock_quantity: 5,
    is_purchasable: true,
  };
}
afterEach(async () => {
  await act(async () => referralContext.reset());
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});
it.each(['product', 'error', 'related'])(
  'authority redirect ignores late hint %s, cart stays authoritative',
  async (late) => {
    vi.stubEnv('VITE_EMPLOYEE_REFERRAL_ENTRY', 'true');
    referralContext.reset();
    useCartStore.setState({ items: [] });
    window.history.replaceState({}, '', '/product/40?ref=' + token);
    const hint = deferred(),
      resolve = deferred(),
      related = deferred();
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
      (input) => {
        const url = String(input);
        if (url.endsWith('/employee-referrals/resolve')) return resolve.promise;
        if (url.endsWith('/products/40'))
          return late === 'related' ? Promise.resolve(json(product(40))) : hint.promise;
        if (url.endsWith('/products/39')) return Promise.resolve(json(product(39)));
        if (url.includes('categoryId=40')) return related.promise;
        return Promise.resolve(json({ items: [] }));
      }
    );
    vi.stubGlobal('fetch', fetchMock);
    render(
      <StrictMode>
        <BrowserRouter>
          <ReferralOwner />
          <Routes>
            <Route path="/product/:productId" element={<ProductPage />} />
          </Routes>
        </BrowserRouter>
      </StrictMode>
    );
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith('/products/40'))).toBe(true)
    );
    if (late === 'related')
      await waitFor(() =>
        expect(fetchMock.mock.calls.some(([url]) => String(url).includes('categoryId=40'))).toBe(
          true
        )
      );
    await act(async () =>
      resolve.finish(
        json({
          state: 'valid',
          product_id: '39',
          observed_at: new Date().toISOString().replace('Z', '000Z'),
          expires_at: new Date(Date.now() + 3600000).toISOString().replace('Z', '000Z'),
        })
      )
    );
    await screen.findByRole('heading', { name: 'Product 39' });
    expect(window.location.pathname).toBe('/product/39');
    await act(async () => {
      if (late === 'related') related.finish(json({ items: [product(41)] }));
      else hint.finish(late === 'error' ? json({}, 503) : json(product(40)));
    });
    expect(screen.getByRole('heading', { name: 'Product 39' })).toBeVisible();
    expect(screen.queryByText('Product 41')).toBeNull();
    expect(window.location.pathname).toBe('/product/39');
    fireEvent.click(screen.getByText('В корзину'));
    expect(useCartStore.getState().items.map((item) => item.productId)).toEqual(['39']);
    expect(referralContext.tokenForOrder()).toBe(token);
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/employee-referrals/resolve'))
    ).toHaveLength(1);
  }
);

it.each([
  [401, 'identity'],
  [403, 'identity'],
  [401, 'entry'],
  [403, 'entry'],
  [401, 'current'],
  [403, 'current'],
] as const)('HTTP%s auth reset belongs to the request %s', async (status, change) => {
  const { default: WebApp } = await import('@twa-dev/sdk');
  const { api } = await import('@/api/client');
  vi.stubEnv('VITE_EMPLOYEE_REFERRAL_ENTRY', 'true');
  Object.defineProperty(WebApp, 'initData', { value: 'synthetic-identity-A', writable: true });
  window.history.replaceState({}, '', '/cart');
  referralContext.reset();
  const old = deferred();
  const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(
    (input) => {
      if (String(input).endsWith('/loyalty/me')) return old.promise;
      return Promise.resolve(
        json({
          state: 'valid',
          product_id: '39',
          observed_at: new Date().toISOString().replace('Z', '000Z'),
          expires_at: new Date(Date.now() + 3600000).toISOString().replace('Z', '000Z'),
        })
      );
    }
  );
  vi.stubGlobal('fetch', fetchMock);
  render(
    <BrowserRouter>
      <ReferralOwner />
    </BrowserRouter>
  );
  await act(async () => referralContext.enter([token]));
  expect(referralContext.tokenForOrder()).toBe(token);
  const pending = api.get('/loyalty/me').catch(() => {});
  expect(new Headers(fetchMock.mock.calls.at(-1)?.[1]?.headers).get('X-Init-Data')).toBe(
    'synthetic-identity-A'
  );
  const B = 'AQEBAQEBAQEBAQEBAQEBAQ';
  if (change === 'identity')
    Object.defineProperty(WebApp, 'initData', { value: 'synthetic-identity-B', writable: true });
  if (change !== 'current') {
    await act(async () => referralContext.enter([B]));
    expect(referralContext.tokenForOrder()).toBe(B);
  }
  await act(async () => {
    old.finish(json({}, status));
    await pending;
  });
  expect(referralContext.snapshot()).toBe(change === 'current' ? 'empty' : 'active');
  expect(referralContext.tokenForOrder()).toBe(change === 'current' ? undefined : B);
  Object.defineProperty(WebApp, 'initData', { value: '', writable: true });
});
