import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';

import { loyaltyApi } from '@/api';
import type { LoyaltySummary } from '@/types';
import { LoyaltyCard } from './LoyaltyCard';

vi.mock('@/api', () => ({
  loyaltyApi: { getMe: vi.fn() },
}));

const getMeMock = vi.mocked(loyaltyApi.getMe);

function renderCard(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const newbie: LoyaltySummary = {
  tier: 'newbie',
  balance: 5800,
  available: 5800,
  reservedUntil: null,
  lifetimeActivations: 3,
  activationsToFriend: 7,
  isMember: true,
};

const friend: LoyaltySummary = {
  tier: 'biotact_friend',
  balance: 9000,
  available: 9000,
  reservedUntil: null,
  lifetimeActivations: 14,
  activationsToFriend: 0,
  isMember: true,
};

describe('LoyaltyCard', () => {
  beforeEach(() => {
    getMeMock.mockReset();
  });

  it('renders newbie balance, badge and progress to friend', async () => {
    getMeMock.mockResolvedValue(newbie);
    renderCard(<LoyaltyCard />);

    expect(await screen.findByText('Новичок')).toBeInTheDocument();
    const balance = screen.getByTestId('loyalty-balance');
    expect(balance.textContent?.replace(/\s/g, '')).toBe('5800');
    // goal is server-derived (lifetime 3 + remaining 7 = 10) — no client threshold constant
    expect(screen.getByText(/3 \/ 10/)).toBeInTheDocument();
    expect(screen.getByText(/активаций до «Друга»/)).toBeInTheDocument();
    expect(screen.queryByText('Друг BIOTACT')).not.toBeInTheDocument();
  });

  it('renders friend badge and perk without a progress bar', async () => {
    getMeMock.mockResolvedValue(friend);
    renderCard(<LoyaltyCard />);

    expect(await screen.findByText('Друг BIOTACT')).toBeInTheDocument();
    expect(screen.getByText('Кэшбэк 8% навсегда')).toBeInTheDocument();
    expect(screen.queryByText(/активаций до «Друга»/)).not.toBeInTheDocument();
  });

  it('stays a newbie progress when the counter is already past the threshold (NIT-1)', async () => {
    getMeMock.mockResolvedValue({
      tier: 'newbie',
      balance: 0,
      lifetimeActivations: 12,
      activationsToFriend: 0,
      isMember: true,
    });
    renderCard(<LoyaltyCard />);

    expect(await screen.findByText('Новичок')).toBeInTheDocument();
    expect(screen.queryByText('Друг BIOTACT')).not.toBeInTheDocument();
    // server goal = lifetime 12 + remaining 0 → full bar "12 / 12", still newbie — no "Друг" claim
    expect(screen.getByText(/12 \/ 12/)).toBeInTheDocument();
  });

  it('shows the full balance plus a reserve note when part of it is pinned', async () => {
    // The prod defect this locks: the profile showed the full balance with no hint that part of it
    // was pinned by an abandoned order, so "Недостаточно баллов" at checkout looked like a bug.
    getMeMock.mockResolvedValue({ ...newbie, available: 500, reservedUntil: null });
    renderCard(<LoyaltyCard />);

    const balance = await screen.findByTestId('loyalty-balance');
    expect(balance.textContent?.replace(/\s/g, '')).toBe('5800'); // still theirs — not burned
    expect(screen.getByTestId('loyalty-reserved').textContent?.replace(/\s/g, '')).toContain('5300');
  });

  it('shows no reserve note when the whole balance is spendable', async () => {
    getMeMock.mockResolvedValue(newbie); // available === balance
    renderCard(<LoyaltyCard />);

    await screen.findByTestId('loyalty-balance');
    expect(screen.queryByTestId('loyalty-reserved')).not.toBeInTheDocument();
  });

  it('renders nothing while the program is dark (endpoint 404 / error)', async () => {
    getMeMock.mockRejectedValue(new Error('HTTP 404'));
    renderCard(<LoyaltyCard />);

    await waitFor(() => {
      expect(getMeMock).toHaveBeenCalled();
    });
    expect(screen.queryByText('Biotact Club')).not.toBeInTheDocument();
  });
});
