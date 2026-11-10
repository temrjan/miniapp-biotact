import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { api } from '@/api/client';
import { MainLayout } from '@components/layouts/MainLayout';
import { useLanguageStore } from '@/stores/languageStore';
import { EVENT_PROMOTION_KEY } from '@hooks/useEventPromotion';

vi.mock('@/api/client', async (original) => {
  const actual = await original<typeof import('@/api/client')>();
  return { ...actual, api: { ...actual.api, get: vi.fn() } };
});

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/catalog']}>
        <MainLayout>
          <p>PAGE</p>
        </MainLayout>
      </MemoryRouter>
    </QueryClientProvider>
  );
  return client;
}

// event-qr-discount R-50: the banner on MainLayout pages, shown exactly while R-20 reports an
// active grant. The date is the last valid Tashkent day: ends_at 2026-12-31T19:00Z is
// 2027-01-01 00:00 in Tashkent, so the discount holds through 31.12.2026.
describe('R-50 event discount banner', () => {
  beforeEach(() => {
    useLanguageStore.setState({ language: 'ru' });
    vi.mocked(api.get).mockReset();
  });

  it('shows the discount while the grant is active', async () => {
    vi.mocked(api.get).mockResolvedValue({
      active: true,
      title: 'NUTRIFORUM 2026',
      percent: 5,
      ends_at: '2026-12-31T19:00:00Z',
    });
    mount();
    expect(await screen.findByRole('status')).toHaveTextContent(
      '🎁 NUTRIFORUM 2026: ваша скидка 5% до 31.12.2026 — применится в корзине'
    );
    expect(api.get).toHaveBeenCalledWith('/event-promotions/me');
    expect(screen.getByText('PAGE')).toBeInTheDocument();
  });

  it.each([
    ['no active grant', { active: false }],
    ['a malformed active body', { active: true, title: 'NUTRIFORUM 2026', percent: '5', ends_at: '2026-12-31T19:00:00Z' }],
    ['a failed read', null],
  ])('shows nothing for %s', async (_case, body) => {
    if (body === null) vi.mocked(api.get).mockRejectedValue(new Error('offline'));
    else vi.mocked(api.get).mockResolvedValue(body);
    const client = mount();
    // The read has settled, so the banner has had its chance to render.
    await waitFor(() => expect(client.getQueryState(EVENT_PROMOTION_KEY)?.status).not.toBe('pending'));
    expect(api.get).toHaveBeenCalledWith('/event-promotions/me');
    expect(screen.getByText('PAGE')).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});
