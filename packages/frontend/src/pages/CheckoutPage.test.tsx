import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { useCartStore } from '@/stores/cartStore';
import { useUserStore } from '@/stores/userStore';
import { useLanguageStore } from '@/stores/languageStore';
import { isLegalEnabled } from '@/legal/flag';
import { translations } from '@/i18n';
import type { Product } from '@/types';
import WebApp from '@twa-dev/sdk';
import { api, ApiError } from '@/api/client';
import { CheckoutPage } from './CheckoutPage';

// Only the network and browser dialogs are replaced; hooks and stores run real.
vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>();
  return { ...actual, api: { ...actual.api, get: vi.fn(), post: vi.fn() } };
});
const postMock = vi.mocked(api.post);
const orders = () => postMock.mock.calls.filter(([url]) => url === '/orders');
const quotes = () => postMock.mock.calls.filter(([url]) => url === '/orders/quote');
let serverTotal: number;
let serverMax: number;
let loyaltyBalance: number;
let conflict: 'quote_mismatch' | 'quote_required' | null;
let quoteFailure: Error | null;
let catalogPrice = { price: 10000, old_price: null as number | null, is_sale: false, sale_price: null as number | null };

function quote(points = 0) {
  return {
    lines: [
      {
        product_id: 1,
        product_name: 'Test Product',
        quantity: 1,
        unit_price: serverTotal.toFixed(2),
        line_total: serverTotal.toFixed(2),
      },
    ],
    subtotal: serverTotal.toFixed(2),
    delivery_fee: '0.00',
    total: serverTotal.toFixed(2),
    max_redeemable: serverMax,
    points_to_apply: points,
    quote_fingerprint: `quote-${serverTotal}-${points}`,
  };
}
const profile = {
  id: 1,
  telegram_id: 123,
  username: 'test',
  first_name: 'Test',
  last_name: null,
  phone: '+998901234567',
  language_code: 'ru',
  is_active: true,
  address: null,
};

function payButton() {
  return screen.getAllByRole('button', { name: /Оплатить/ })[0];
}

const product: Product = {
  id: '1',
  name: 'Test Product',
  description: '',
  price: 10000,
  imageUrl: '',
  categoryId: 'c1',
  inStock: true,
};

const ru = translations.ru;

function renderCheckout() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/checkout']}>
        <Routes>
          <Route path="/checkout" element={<CheckoutPage />} />
          <Route path="/payment/success" element={<div>PAYMENT_SUCCESS</div>} />
          <Route path="/cart" element={<div>CART_SCREEN</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

async function walkToConfirmStep() {
  renderCheckout();

  // Profile phone is complete, so the page auto-advances phone → delivery.
  const pickupCard = await screen.findByText(ru.checkout.pickup);
  fireEvent.click(pickupCard);

  // Pickup skips the address step: delivery → confirm.
  const placeOrderButtons = await screen.findAllByRole('button', {
    name: ru.checkout.placeOrder,
  });
  fireEvent.click(placeOrderButtons[0]);

  expect(await screen.findByText(ru.checkout.orderConfirmation)).toBeInTheDocument();
}

beforeEach(() => {
  catalogPrice = { price: 10000, old_price: null, is_sale: false, sale_price: null };
  serverTotal = 10000;
  serverMax = 10000;
  loyaltyBalance = 20000;
  conflict = null;
  quoteFailure = null;
  useLanguageStore.setState({ language: 'ru' });
  useCartStore.setState({ items: [{ productId: product.id, product, quantity: 1 }] });
  useUserStore.setState({ profile, isLoading: false });
  vi.spyOn(window, 'alert')
    .mockReset()
    .mockImplementation(() => {});
  vi.spyOn(window, 'confirm').mockReset().mockReturnValue(false);
  vi.mocked(WebApp.openLink).mockClear();
  vi.mocked(api.get).mockImplementation(async (url) => {
    if (url === '/products/1') return catalogPrice;
    if (url === '/users/me') return profile;
    if (url === '/loyalty/me')
      return {
        balance: loyaltyBalance,
        available: loyaltyBalance,
        is_member: true,
        tier: 'friend',
        lifetime_activations: 1,
        activations_to_friend: 0,
      };
    throw new Error(`Unexpected GET ${url}`);
  });
  postMock.mockReset();
  postMock.mockImplementation(async (url, data) => {
    const points = (data as { points_to_apply?: number }).points_to_apply ?? 0;
    if (url === '/orders/quote') {
      if (quoteFailure) throw quoteFailure;
      return quote(points);
    }
    if (url === '/orders') {
      if (conflict)
        throw new ApiError('changed', 409, { detail: { code: conflict, quote: quote(points) } });
      return {
        id: 1,
        order_number: 'ORD-1',
        total: serverTotal,
        payment_status: points === serverTotal ? 'paid' : 'unpaid',
        points_applied: points,
      };
    }
    if (url === '/payments/create')
      return { payment_url: 'https://pay.test/1', payment_uuid: 'p1', order_id: 1 };
    throw new Error(`Unexpected POST ${url}`);
  });
});

describe('CheckoutPage — accept-terms line (VITE_LEGAL_DOCS)', () => {
  // The suite runs twice: default (OFF) asserts the line does NOT leak into the current
  // checkout; the ON run (VITE_LEGAL_DOCS=true) asserts it renders above the pay button.
  it('shows the terms-acceptance line in the totals block exactly when the flag is on', async () => {
    await walkToConfirmStep();

    if (isLegalEnabled()) {
      expect(screen.getByText(ru.checkout.acceptTermsPrefix)).toBeInTheDocument();
      const link = screen.getByRole('link', { name: ru.checkout.acceptTermsLink });
      expect(link).toHaveAttribute('href', '/legal/terms');
    } else {
      expect(screen.queryByText(ru.checkout.acceptTermsPrefix)).not.toBeInTheDocument();
      expect(
        screen.queryByRole('link', { name: ru.checkout.acceptTermsLink })
      ).not.toBeInTheDocument();
    }
  });
});

describe('CheckoutPage — quote agreement', () => {
  it.each([12000, 8000, 10000.5])(
    'should require confirmation when total changes to %s',
    async (total) => {
      serverTotal = total;
      await walkToConfirmStep();
      const before = useCartStore.getState().items;
      fireEvent.click(payButton());
      await waitFor(() => expect(window.confirm).toHaveBeenCalled());
      expect(window.confirm).toHaveBeenCalledWith(
        expect.stringContaining(total.toLocaleString('ru-RU'))
      );
      expect(orders()).toHaveLength(0);
      expect(WebApp.openLink).not.toHaveBeenCalled();
      expect(useCartStore.getState().items).toEqual(before);
      expect(
        screen.getAllByText(
          `${total.toLocaleString('ru-RU')} ${ru.currency.sum}`.replace(/\u00a0/g, ' ')
        ).length
      ).toBeGreaterThan(0);
      // A new explicit click accepts the now-visible price, after a fresh quote.
      fireEvent.click(payButton());
      await waitFor(() => expect(WebApp.openLink).toHaveBeenCalled());
      expect(quotes()).toHaveLength(2);
      expect(orders()).toHaveLength(1);
      expect(orders()[0]?.[1]).toMatchObject({ quote_fingerprint: `quote-${total}-0` });
    }
  );

  it('should settle only after the buyer accepts the changed price', async () => {
    serverTotal = 12000;
    vi.mocked(window.confirm).mockImplementation(() => {
      expect(orders()).toHaveLength(0);
      return true;
    });
    await walkToConfirmStep();
    fireEvent.click(payButton());
    await waitFor(() => expect(WebApp.openLink).toHaveBeenCalled());
    expect(window.confirm).toHaveBeenCalledOnce();
    expect(orders()[0]?.[1]).toMatchObject({ quote_fingerprint: 'quote-12000-0' });
  });

  it('should not create a full-points order or navigate when changed price is declined', async () => {
    await walkToConfirmStep();
    fireEvent.click(await screen.findByRole('button', { name: ru.checkout.redeem.max }));
    serverTotal = 16000; // 10,000 points still leaves a valid card remainder.
    serverMax = 16000;
    fireEvent.click(payButton());
    await waitFor(() => expect(window.confirm).toHaveBeenCalled());
    expect(orders()).toHaveLength(0);
    expect(screen.queryByText('PAYMENT_SUCCESS')).not.toBeInTheDocument();
    expect(WebApp.openLink).not.toHaveBeenCalled();
  });

  it.each([
    ['quote_mismatch', 8000, 8000],
    ['quote_required', 8000, 8000],
    ['quote_mismatch', 10000, 2000],
    ['quote_mismatch', 12000, 12000],
  ] as const)(
    'should show fresh terms after %s (total %s, ceiling %s) without an automatic retry',
    async (code, newTotal, newMax) => {
      await walkToConfirmStep();
      fireEvent.click(await screen.findByRole('button', { name: ru.checkout.redeem.max }));
      // The price changes only AFTER the quote, before order creation.
      const network = postMock.getMockImplementation();
      if (!network) throw new Error('Missing API fixture');
      postMock.mockImplementation(async (url, data) => {
        if (url === '/orders') {
          serverTotal = newTotal;
          serverMax = newMax;
          conflict = code;
        }
        return network(url, data);
      });
      fireEvent.click(payButton());
      await waitFor(() => expect(window.alert).toHaveBeenCalled());
      expect(orders()).toHaveLength(1);
      expect(quotes()).toHaveLength(1);
      expect(screen.queryByText('PAYMENT_SUCCESS')).not.toBeInTheDocument();
      expect(screen.getByRole('slider')).toHaveAttribute('max', String(newMax));
      expect(screen.getByRole('slider')).toHaveValue('0');
      expect(
        screen.getAllByText(
          `${newTotal.toLocaleString('ru-RU')} ${ru.currency.sum}`.replace(/\u00a0/g, ' ')
        ).length
      ).toBeGreaterThan(0);
      expect(screen.getByText(/Баланс: 20\s000/)).toBeInTheDocument();
      expect(WebApp.openLink).not.toHaveBeenCalled();
      conflict = null;
      postMock.mockImplementation((url, data) => network(url, data));
      fireEvent.click(payButton());
      await waitFor(() => expect(WebApp.openLink).toHaveBeenCalled());
      expect(orders()).toHaveLength(2);
      expect(quotes()).toHaveLength(2);
      expect(orders()[1]?.[1]).toMatchObject({
        points_to_apply: 0,
        quote_fingerprint: `quote-${newTotal}-0`,
      });
    }
  );

  it.each([new ApiError('timeout', 408), new Error('offline')])(
    'should preserve the cart and refuse settlement when quote fails: %s',
    async (error) => {
      quoteFailure = error;
      await walkToConfirmStep();
      const before = useCartStore.getState().items;
      fireEvent.click(payButton());
      await waitFor(() => expect(window.alert).toHaveBeenCalled());
      expect(quotes()).toHaveLength(1);
      expect(orders()).toHaveLength(0);
      expect(useCartStore.getState().items).toEqual(before);
      quoteFailure = null;
      fireEvent.click(payButton());
      await waitFor(() => expect(WebApp.openLink).toHaveBeenCalled());
      expect(orders()).toHaveLength(1);
    }
  );

  it.each(['cart', 'points'] as const)(
    'should discard an in-flight quote when %s changes',
    async (change) => {
      let resolveQuote: ((value: ReturnType<typeof quote>) => void) | undefined;
      postMock.mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveQuote = resolve;
          })
      );
      await walkToConfirmStep();
      fireEvent.click(payButton());
      if (change === 'cart') {
        act(() => useCartStore.getState().updateQuantity('1', 2));
      } else {
        fireEvent.click(screen.getByRole('button', { name: ru.checkout.redeem.max }));
      }
      await act(async () => {
        resolveQuote?.(quote());
      });
      expect(orders()).toHaveLength(0);
      expect(WebApp.openLink).not.toHaveBeenCalled();
      if (change === 'cart') {
        serverTotal = 20000;
        serverMax = 20000;
      }
      fireEvent.click(payButton());
      await waitFor(() => expect(orders()).toHaveLength(1));
      expect(quotes()).toHaveLength(2);
      expect(orders()[0]?.[1]).toMatchObject({
        items: [{ product_id: 1, quantity: change === 'cart' ? 2 : 1 }],
        points_to_apply: change === 'points' ? 10000 : 0,
        quote_fingerprint: change === 'points' ? 'quote-10000-10000' : 'quote-20000-0',
      });
    }
  );
});


describe('CheckoutPage — current catalog prices', () => {
  it('should display the current sale total when entered with a stale persisted cart', async () => {
    catalogPrice = { price: 10000, old_price: 12000, is_sale: true, sale_price: 8000.5 };
    serverTotal = 8000.5;
    serverMax = 0;
    loyaltyBalance = 0;
    await walkToConfirmStep();
    expect(await screen.findByText('8 000,5 сум')).toBeInTheDocument();
    fireEvent.click(payButton());
    await waitFor(() => expect(WebApp.openLink).toHaveBeenCalled());
    expect(window.confirm).not.toHaveBeenCalled();
    expect(orders()[0]?.[1]).toMatchObject({ quote_fingerprint: 'quote-8000.5-0' });
  });
});

// Regression coverage for step-6 Reviewer B1/B2. Deferred network promises fix
// response ordering without timing sleeps; the real page/payment/store execute.
describe('CheckoutPage — delayed catalog regressions', () => {
  function delayCatalog() {
    const network = vi.mocked(api.get).getMockImplementation();
    if (!network) throw new Error('Missing GET fixture');
    let resolveCatalog: ((value: typeof catalogPrice) => void) | undefined;
    vi.mocked(api.get).mockImplementation((url) => url === '/products/1'
      ? new Promise(resolve => { resolveCatalog = resolve; }) : network(url));
    return async (price: typeof catalogPrice) => {
      await act(async () => {
        if (!resolveCatalog) throw new Error('Catalog request was not started');
        resolveCatalog(price);
      });
    };
  }

  it.each([
    [10000, 8000, 0],
    [10000, 12000, 0],
    [5000, 8000, 0],
    [10000, 10000, 10000],
    [10000, 16000, 10000],
    [5000, 5000, 5000],
  ])('should keep selection and submitted points valid when %s points meet a delayed total %s', async (selected, total, expectedPoints) => {
    const finishCatalog = delayCatalog();
    const network = postMock.getMockImplementation();
    if (!network) throw new Error('Missing POST fixture');
    postMock.mockImplementation((url, data) => {
      const points = (data as { points_to_apply: number }).points_to_apply;
      const remainder = serverTotal - points;
      if (url === '/orders/quote' && points > 0 && (points > serverMax || (remainder !== 0 && remainder < 5000))) {
        return Promise.reject(new ApiError('invalid', 422, { detail: { max_redeemable: serverMax } }));
      }
      return network(url, data);
    });
    await walkToConfirmStep();
    fireEvent.change(screen.getByRole('slider'), { target: { value: String(selected) } });
    expect(screen.getByRole('slider')).toHaveValue(String(selected));
    serverTotal = total;
    serverMax = total;
    await finishCatalog({ price: 20000, old_price: null, is_sale: true, sale_price: total });
    expect(screen.getByRole('slider')).toHaveValue(String(expectedPoints));
    expect(screen.queryByText(/-2\s000 сум/)).not.toBeInTheDocument();
    fireEvent.click(payButton());
    await waitFor(() => expect(orders()).toHaveLength(1));
    expect(quotes()[0]?.[1]).toMatchObject({ points_to_apply: expectedPoints });
    expect(orders()[0]?.[1]).toMatchObject({ points_to_apply: expectedPoints });
    expect(window.alert).not.toHaveBeenCalled();
  });

  it('should not restore reset points when the cart grows after repricing', async () => {
    const finishCatalog = delayCatalog();
    await walkToConfirmStep();
    fireEvent.click(screen.getByRole('button', { name: ru.checkout.redeem.max }));
    await finishCatalog({ price: 10000, old_price: null, is_sale: true, sale_price: 8000 });
    expect(screen.getByRole('slider')).toHaveValue('0');
    act(() => useCartStore.getState().updateQuantity('1', 2));
    expect(screen.getByRole('slider')).toHaveValue('0');
    expect(orders()).toHaveLength(0);
    expect(quotes()).toHaveLength(0);
  });

  it.each(['quote_mismatch', 'quote_required'] as const)(
    'should preserve fresh %s terms when an older catalog request finishes', async (code) => {
      const finishCatalog = delayCatalog();
      const network = postMock.getMockImplementation();
      if (!network) throw new Error('Missing POST fixture');
      postMock.mockImplementation((url, data) => {
        if (url === '/orders') {
          serverTotal = 8000;
          serverMax = 8000;
          conflict = code;
        }
        return network(url, data);
      });
      await walkToConfirmStep();
      fireEvent.click(payButton());
      await waitFor(() => expect(window.alert).toHaveBeenCalledOnce());
      expect(screen.getAllByText('8 000 сум').length).toBeGreaterThan(0);
      await finishCatalog({ price: 10000, old_price: null, is_sale: false, sale_price: null });
      expect(screen.getAllByText('8 000 сум').length).toBeGreaterThan(0);
      expect(payButton()).toHaveTextContent('Оплатить 8 000 сум');
      expect(orders()).toHaveLength(1);
      expect(quotes()).toHaveLength(1);
      // Actual points edits still require a new quote and an explicit pay click.
      fireEvent.click(screen.getByRole('button', { name: ru.checkout.redeem.max }));
      expect(orders()).toHaveLength(1);
      conflict = null;
      postMock.mockImplementation(network);
      fireEvent.click(payButton());
      await waitFor(() => expect(orders()).toHaveLength(2));
      expect(quotes()).toHaveLength(2);
      expect(orders()[1]?.[1]).toMatchObject({ points_to_apply: 8000, quote_fingerprint: 'quote-8000-8000' });
    }
  );

  it('should preserve a declined fresh quote then invalidate it when the buyer changes cart quantity', async () => {
    const finishCatalog = delayCatalog();
    serverTotal = 8000;
    serverMax = 8000;
    await walkToConfirmStep();
    fireEvent.click(payButton());
    await waitFor(() => expect(window.confirm).toHaveBeenCalledOnce());
    expect(orders()).toHaveLength(0);
    expect(screen.getAllByText('8 000 сум').length).toBeGreaterThan(0);
    await finishCatalog({ price: 12000, old_price: null, is_sale: false, sale_price: null });
    expect(screen.getAllByText('8 000 сум').length).toBeGreaterThan(0);
    act(() => useCartStore.getState().updateQuantity('1', 2));
    expect(screen.queryByText('8 000 сум')).not.toBeInTheDocument();
    expect(orders()).toHaveLength(0);
    serverTotal = 16000;
    serverMax = 16000;
    vi.mocked(window.confirm).mockReturnValue(true);
    fireEvent.click(payButton());
    await waitFor(() => expect(orders()).toHaveLength(1));
    expect(quotes()).toHaveLength(2);
    expect(orders()[0]?.[1]).toMatchObject({ items: [{ product_id: 1, quantity: 2 }], quote_fingerprint: 'quote-16000-0' });
  });
});

// event-qr-discount R-52: with an active grant the confirm step shows the server quote before the
// stepper and pay(). The cart prices the product at 10 000; the server's discounted quote is 9 500
// (5% grant), so any use of the cart's own sum shows up as 10 000 or as a lower points ceiling.
describe('CheckoutPage — R-52 event discount quote', () => {
  beforeEach(() => {
    serverTotal = 9500;
    serverMax = 9500;
    const network = vi.mocked(api.get).getMockImplementation();
    if (!network) throw new Error('Missing GET fixture');
    vi.mocked(api.get).mockImplementation(async (url) =>
      url === '/event-promotions/me'
        ? { active: true, title: 'NUTRIFORUM 2026', percent: 5, ends_at: '2026-12-31T19:00:00Z' }
        : network(url)
    );
  });

  it('keeps pay disabled until the quote loads, then shows and pays its reduced terms', async () => {
    let resolveQuote: ((value: ReturnType<typeof quote>) => void) | undefined;
    const network = postMock.getMockImplementation();
    if (!network) throw new Error('Missing POST fixture');
    postMock.mockImplementation((url, data) =>
      url === '/orders/quote' && !resolveQuote
        ? new Promise((resolve) => {
            resolveQuote = resolve;
          })
        : network(url, data)
    );
    await walkToConfirmStep();
    await waitFor(() => expect(quotes()).toHaveLength(1));
    expect(quotes()[0]?.[1]).toMatchObject({ items: [{ product_id: 1, quantity: 1 }], points_to_apply: 0 });
    expect(payButton()).toBeDisabled();
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
    expect(screen.queryByText('10 000 сум')).not.toBeInTheDocument();
    expect(screen.getByText(ru.cart.total).parentElement).toHaveTextContent(/^Итого$/);

    await act(async () => {
      resolveQuote?.(quote());
    });
    expect(screen.getByText(ru.cart.total).parentElement).toHaveTextContent(/^Итого9\s500 сум$/);
    expect(screen.getByRole('slider')).toHaveAttribute('max', '9500');
    expect(payButton()).toBeEnabled();
    expect(payButton()).toHaveTextContent('Оплатить 9 500 сум');

    fireEvent.click(payButton());
    await waitFor(() => expect(WebApp.openLink).toHaveBeenCalled());
    expect(window.confirm).not.toHaveBeenCalled();
    expect(orders()).toHaveLength(1);
    expect(orders()[0]?.[1]).toMatchObject({ points_to_apply: 0, quote_fingerprint: 'quote-9500-0' });
  });

  it('keeps pay disabled and shows the existing error after a failed quote', async () => {
    quoteFailure = new Error('offline');
    await walkToConfirmStep();
    await waitFor(() => expect(window.alert).toHaveBeenCalledOnce());
    expect(quotes()).toHaveLength(1);
    expect(payButton()).toBeDisabled();
    expect(screen.queryByRole('slider')).not.toBeInTheDocument();
    fireEvent.click(payButton());
    expect(orders()).toHaveLength(0);
    expect(quotes()).toHaveLength(1);
  });
});
