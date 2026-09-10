import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { useCartStore } from '@/stores/cartStore';
import { useUserStore } from '@/stores/userStore';
import { useLanguageStore } from '@/stores/languageStore';
import { isLegalEnabled } from '@/legal/flag';
import { translations } from '@/i18n';
import type { Product } from '@/types';
import { CheckoutPage } from './CheckoutPage';

// The payment and loyalty hooks sit on the API boundary — stubbed there, not in the stores
// (zustand stores run real, per project testing rules).
vi.mock('@hooks/usePayment', () => ({
  usePayment: () => ({ pay: vi.fn(), isProcessing: false }),
}));
vi.mock('@hooks/useLoyalty', () => ({
  useLoyalty: () => ({ data: undefined }),
}));

const product: Product = {
  id: 'p1',
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

describe('CheckoutPage — accept-terms line (VITE_LEGAL_DOCS)', () => {
  beforeEach(() => {
    useLanguageStore.setState({ language: 'ru' });
    useCartStore.setState({ items: [{ productId: product.id, product, quantity: 1 }] });
    useUserStore.setState({
      profile: {
        id: 1,
        telegram_id: 123,
        username: 'test',
        first_name: 'Test',
        last_name: null,
        phone: '+998901234567',
        language_code: 'ru',
        is_active: true,
        address: null,
      },
      fetchProfile: vi.fn().mockResolvedValue(undefined),
    });
  });

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
