import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '@/api/client';
import { useCartStore } from '@/stores/cartStore';
import { usePendingOrderStore } from '@/stores/pendingOrderStore';
import { useLanguageStore } from '@/stores/languageStore';
import { PaymentSuccessPage } from './PaymentSuccessPage';
import type { Product } from '@/types';

vi.mock('@/api/client', () => ({ api: { get: vi.fn() } }));
const vest: Product = {
  id: '39',
  name: 'Vest',
  description: '',
  price: 30000,
  imageUrl: '/vest.png',
  categoryId: '1',
  inStock: true,
  requiresSize: true,
  availableSizes: ['L', 'XL'],
};
let client: QueryClient;
beforeEach(() => {
  vi.mocked(api.get).mockReset();
  useLanguageStore.setState({ language: 'ru' });
  useCartStore.getState().clearCart();
  useCartStore.getState().addItem(vest, 1, 'L');
  usePendingOrderStore.getState().rememberOrder(39, 'size-basket');
});
afterEach(() => {
  client?.clear();
});
function mount() {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/payment/success?order_id=ORD-SIZE&id=39']}>
        <PaymentSuccessPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}
it('reads the own order by internal ID, displays the chosen size and clears a confirmed matching basket', async () => {
  vi.mocked(api.get).mockResolvedValue({
    payment_status: 'paid',
    items: [{ id: 1, product_id: 39, product_name: 'Vest', size: 'L', quantity: 1 }],
  });
  mount();
  expect(await screen.findByText('Vest (L) ×1')).toBeInTheDocument();
  await waitFor(() => expect(useCartStore.getState().items).toEqual([]));
  expect(api.get).toHaveBeenCalledWith('/orders/39');
  expect(usePendingOrderStore.getState().pending).toBeNull();
});
it('an unpaid size order cannot clear the cart through a success URL', async () => {
  vi.mocked(api.get).mockResolvedValue({
    payment_status: 'pending',
    items: [{ id: 1, product_id: 39, product_name: 'Vest', size: 'L', quantity: 1 }],
  });
  mount();
  await screen.findByText('Vest (L) ×1');
  expect(useCartStore.getState().items).toHaveLength(1);
  expect(usePendingOrderStore.getState().pending?.orderId).toBe(39);
});
it('resolving a paid earlier order preserves a newly edited basket', async () => {
  useCartStore.getState().chooseSize('39', 'L', 'XL');
  vi.mocked(api.get).mockResolvedValue({
    payment_status: 'paid',
    items: [{ id: 1, product_id: 39, product_name: 'Vest', size: 'L', quantity: 1 }],
  });
  mount();
  await screen.findByText('Vest (L) ×1');
  await waitFor(() => expect(usePendingOrderStore.getState().pending).toBeNull());
  expect(useCartStore.getState().items[0]).toMatchObject({ size: 'XL', quantity: 1 });
});

it('an earlier paid order cannot clear the same lines held by a newer order', async () => {
  usePendingOrderStore.getState().rememberOrder(40, 'new-size-basket');
  vi.mocked(api.get).mockResolvedValue({
    payment_status: 'paid',
    items: [{ id: 1, product_id: 39, product_name: 'Vest', size: 'L', quantity: 1 }],
  });
  mount();
  await screen.findByText('Vest (L) ×1');
  expect(useCartStore.getState().items[0]).toMatchObject({ size: 'L', quantity: 1 });
  expect(usePendingOrderStore.getState().pending?.orderId).toBe(40);
});
