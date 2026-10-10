import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import WebApp from '@twa-dev/sdk';
import { beforeEach, expect, it, vi } from 'vitest';
import { api, ApiError } from '@/api/client';
import { usePendingOrderStore } from '@/stores/pendingOrderStore';
import { useLanguageStore } from '@/stores/languageStore';
import { OrdersPage } from './OrdersPage';

vi.mock('@/api/client', async (original) => {
  const actual = await original<typeof import('@/api/client')>();
  return { ...actual, api: { ...actual.api, get: vi.fn(), post: vi.fn() } };
});
const order = {
  id: 39,
  order_number: 'ORD-SIZE',
  status: 'pending',
  payment_status: 'pending',
  total: '90000.00',
  created_at: '2026-10-09T00:00:00Z',
  items: ['L', 'XL', '2XL'].map((size, i) => ({
    id: i + 1,
    product_id: 39,
    product_name: 'Vest',
    product_price: '30000.00',
    quantity: 1,
    total: '30000.00',
    size,
  })),
};
beforeEach(() => {
  usePendingOrderStore.getState().forgetOrder();
  useLanguageStore.setState({ language: 'ru' });
  vi.mocked(api.get)
    .mockReset()
    .mockResolvedValue({ items: [order] });
  vi.mocked(api.post).mockReset();
  vi.mocked(WebApp.openLink).mockClear();
});
function mount() {
  return render(
    <MemoryRouter>
      <OrdersPage />
    </MemoryRouter>
  );
}

it('shows every chosen size when one vest order contains three sizes', async () => {
  mount();
  expect(await screen.findByText('Vest (2XL) x1')).toBeInTheDocument();
  expect(screen.getByText('Vest (L) x1')).toBeInTheDocument();
  expect(screen.getByText('Vest (XL) x1')).toBeInTheDocument();
});

it('resumes a listed size order with one payment request and no new order admission', async () => {
  vi.mocked(api.post).mockResolvedValue({ payment_url: 'https://payment.example/original' });
  usePendingOrderStore.getState().markCreationUnknown();
  mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Продолжить оплату' }));
  await waitFor(() =>
    expect(WebApp.openLink).toHaveBeenCalledWith('https://payment.example/original')
  );
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.post).toHaveBeenCalledWith('/payments/create', {
    order_id: 39,
    return_url: `${window.location.origin}/payment/success?order_id=ORD-SIZE&id=39`,
  });
  expect(usePendingOrderStore.getState().creationUnknown).toBe(false);
  expect(usePendingOrderStore.getState().pending?.orderId).toBe(39);
});

it('a held payment result preserves the saved order and explains the reservation', async () => {
  vi.mocked(api.post).mockRejectedValue(
    new ApiError('Held', 409, {
      detail: { code: 'payment_cancellation_pending', order_id: 39 },
    })
  );
  mount();
  fireEvent.click(await screen.findByRole('button', { name: 'Продолжить оплату' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Размер и баллы остаются в резерве');
  expect(usePendingOrderStore.getState().pending?.orderId).toBe(39);
  expect(WebApp.openLink).not.toHaveBeenCalled();
});
