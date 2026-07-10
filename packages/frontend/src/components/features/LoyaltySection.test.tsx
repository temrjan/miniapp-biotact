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
// The survey sentinel echoes its props — the member test asserts the saved survey is passed down.
vi.mock('./SurveyCard', () => ({
  SurveyCard: (props: { gender: string | null; age: number | null; city: string | null }) => (
    <div>SURVEY_CARD {JSON.stringify(props)}</div>
  ),
}));

const getMeMock = vi.mocked(loyaltyApi.getMe);

const base: LoyaltySummary = {
  tier: 'newbie',
  balance: 0,
  lifetimeActivations: 0,
  activationsToFriend: 10,
  isMember: false,
  gender: null,
  age: null,
  city: null,
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

  it('renders the loyalty card + survey for a member', async () => {
    getMeMock.mockResolvedValue({ ...base, isMember: true });
    renderSection();

    expect(await screen.findByText('LOYALTY_CARD')).toBeInTheDocument();
    expect(screen.getByText(/SURVEY_CARD/)).toBeInTheDocument();
    expect(screen.queryByText('JOIN_CARD')).not.toBeInTheDocument();
  });

  it('passes the saved survey down to the survey card', async () => {
    getMeMock.mockResolvedValue({
      ...base,
      isMember: true,
      gender: 'male',
      age: 47,
      city: 'Ташкент',
    });
    renderSection();

    const sentinel = await screen.findByText(/SURVEY_CARD/);
    expect(sentinel).toHaveTextContent('"gender":"male"');
    expect(sentinel).toHaveTextContent('"age":47');
    expect(sentinel).toHaveTextContent('"city":"Ташкент"');
  });

  it('renders the join CTA (no survey) for a non-member', async () => {
    getMeMock.mockResolvedValue({ ...base, isMember: false });
    renderSection();

    expect(await screen.findByText('JOIN_CARD')).toBeInTheDocument();
    expect(screen.queryByText('LOYALTY_CARD')).not.toBeInTheDocument();
    expect(screen.queryByText(/SURVEY_CARD/)).not.toBeInTheDocument();
  });

  it('renders nothing while the program is dark (endpoint 404)', async () => {
    getMeMock.mockRejectedValue(new Error('HTTP 404'));
    renderSection();

    await waitFor(() => expect(getMeMock).toHaveBeenCalled());
    expect(screen.queryByText('LOYALTY_CARD')).not.toBeInTheDocument();
    expect(screen.queryByText('JOIN_CARD')).not.toBeInTheDocument();
    expect(screen.queryByText(/SURVEY_CARD/)).not.toBeInTheDocument();
  });
});
