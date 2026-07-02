import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { loyaltyApi } from '@/api';
import type { LoyaltySummary } from '@/types';
import { LoyaltySection } from './LoyaltySection';

vi.mock('@/api', () => ({ loyaltyApi: { getMe: vi.fn() } }));
// Child components are stubbed to sentinels — this suite asserts the swap, not their internals.
vi.mock('./LoyaltyCard', () => ({ LoyaltyCard: () => <div>LOYALTY_CARD</div> }));
vi.mock('./JoinCard', () => ({ JoinCard: () => <div>JOIN_CARD</div> }));

const getMeMock = vi.mocked(loyaltyApi.getMe);

const base: LoyaltySummary = {
  tier: 'newbie',
  balance: 0,
  lifetimeActivations: 0,
  activationsToFriend: 10,
  isMember: false,
};

function renderSection() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <LoyaltySection />
    </QueryClientProvider>
  );
}

describe('LoyaltySection', () => {
  beforeEach(() => {
    getMeMock.mockReset();
  });

  it('renders the loyalty card for a member', async () => {
    getMeMock.mockResolvedValue({ ...base, isMember: true });
    renderSection();

    expect(await screen.findByText('LOYALTY_CARD')).toBeInTheDocument();
    expect(screen.queryByText('JOIN_CARD')).not.toBeInTheDocument();
  });

  it('renders the join CTA for a non-member', async () => {
    getMeMock.mockResolvedValue({ ...base, isMember: false });
    renderSection();

    expect(await screen.findByText('JOIN_CARD')).toBeInTheDocument();
    expect(screen.queryByText('LOYALTY_CARD')).not.toBeInTheDocument();
  });

  it('renders nothing while the program is dark (endpoint 404)', async () => {
    getMeMock.mockRejectedValue(new Error('HTTP 404'));
    renderSection();

    await waitFor(() => expect(getMeMock).toHaveBeenCalled());
    expect(screen.queryByText('LOYALTY_CARD')).not.toBeInTheDocument();
    expect(screen.queryByText('JOIN_CARD')).not.toBeInTheDocument();
  });
});
