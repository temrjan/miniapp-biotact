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
  lifetimeActivations: 3,
  activationsToFriend: 7,
};

const friend: LoyaltySummary = {
  tier: 'biotact_friend',
  balance: 9000,
  lifetimeActivations: 14,
  activationsToFriend: 0,
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
    });
    renderCard(<LoyaltyCard />);

    expect(await screen.findByText('Новичок')).toBeInTheDocument();
    expect(screen.queryByText('Друг BIOTACT')).not.toBeInTheDocument();
    // capped at 10/10, framed as newbie progress — never an "almost Друг" claim
    expect(screen.getByText(/10 \/ 10/)).toBeInTheDocument();
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
