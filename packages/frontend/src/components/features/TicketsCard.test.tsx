import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';

import { loyaltyApi } from '@/api';
import type { MyTickets } from '@/types';
import { TicketsCard } from './TicketsCard';

vi.mock('@/api', () => ({
  loyaltyApi: { getMyTickets: vi.fn() },
}));

const getMyTicketsMock = vi.mocked(loyaltyApi.getMyTickets);

function renderCard(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const prospective: MyTickets = {
  tickets: 2,
  isFrozen: false,
  period: null,
  prizeLabel: null,
  result: null,
};

const frozen: MyTickets = {
  tickets: 2,
  isFrozen: true,
  period: '2026-07',
  prizeLabel: 'Аэрогриль',
  result: null,
};

describe('TicketsCard', () => {
  beforeEach(() => {
    getMyTicketsMock.mockReset();
  });

  it('shows a live count while nothing is frozen yet', async () => {
    getMyTicketsMock.mockResolvedValue(prospective);
    renderCard(<TicketsCard />);

    const count = await screen.findByTestId('tickets-count');
    expect(count.textContent).toContain('2');
    expect(screen.getByTestId('tickets-state').textContent).toMatch(/Сейчас/);
  });

  it('shows a fixed count once the snapshot is frozen', async () => {
    getMyTicketsMock.mockResolvedValue(frozen);
    renderCard(<TicketsCard />);

    const state = await screen.findByTestId('tickets-state');
    expect(state.textContent).toMatch(/участвуете/);
    expect(screen.getByText(/Аэрогриль/)).toBeInTheDocument();
  });

  it('words the SAME number differently depending on whether it is frozen', async () => {
    // The whole point of this PR: a live recount can still change on its own, a frozen one cannot.
    // Identical numbers must not produce an identical screen, otherwise "2 tickets" silently
    // becoming "1" reads to the member as tickets being taken away.
    getMyTicketsMock.mockResolvedValue(prospective);
    const live = renderCard(<TicketsCard />);
    const liveText = (await screen.findByTestId('tickets-state')).textContent;
    live.unmount();

    getMyTicketsMock.mockResolvedValue(frozen);
    renderCard(<TicketsCard />);
    const frozenText = (await screen.findByTestId('tickets-state')).textContent;

    expect(prospective.tickets).toBe(frozen.tickets); // same number...
    expect(liveText).not.toBe(frozenText); // ...different sentence
  });

  it('explains how to earn one when the member has no tickets', async () => {
    getMyTicketsMock.mockResolvedValue({ ...prospective, tickets: 0 });
    renderCard(<TicketsCard />);

    expect(await screen.findByTestId('tickets-howto')).toBeInTheDocument();
    expect(screen.queryByTestId('tickets-count')).not.toBeInTheDocument();
  });

  it('reports a win from the finished draw alongside the next-period count', async () => {
    getMyTicketsMock.mockResolvedValue({
      ...prospective,
      tickets: 1,
      result: {
        period: '2026-07',
        tickets: 2,
        won: true,
        prizeRank: 1,
        prizeLabel: 'Аэрогриль',
      },
    });
    renderCard(<TicketsCard />);

    const block = await screen.findByTestId('tickets-result');
    expect(block).toHaveTextContent(/выиграли/);
    // What they won, not just that they won — the rank and the prize are already on the wire,
    // withholding them makes the card announce a victory and then refuse to name it.
    expect(block).toHaveTextContent('Аэрогриль');
    expect(block).toHaveTextContent('№1');
    // the prospective block for the NEXT draw must survive beside the result
    expect(screen.getByTestId('tickets-count').textContent).toContain('1');
  });

  it('names no prize when the member did not win', async () => {
    getMyTicketsMock.mockResolvedValue({
      ...prospective,
      result: {
        period: '2026-07',
        tickets: 2,
        won: false,
        prizeRank: null,
        prizeLabel: 'Аэрогриль',
      },
    });
    renderCard(<TicketsCard />);

    const block = await screen.findByTestId('tickets-result');
    // The draw had a prize; this member did not get it. Showing the prize label beside a loss
    // would read as if they had won it.
    expect(block).not.toHaveTextContent('Аэрогриль');
  });

  it('reports a loss as an outcome, not as an absence', async () => {
    getMyTicketsMock.mockResolvedValue({
      ...prospective,
      result: {
        period: '2026-07',
        tickets: 2,
        won: false,
        prizeRank: null,
        prizeLabel: 'Аэрогриль',
      },
    });
    renderCard(<TicketsCard />);

    expect(await screen.findByTestId('tickets-result')).toHaveTextContent(/не повезло/);
  });

  it('renders nothing while the program is dark (endpoint 404 / error)', async () => {
    getMyTicketsMock.mockRejectedValue(new Error('HTTP 404'));
    renderCard(<TicketsCard />);

    await waitFor(() => {
      expect(getMyTicketsMock).toHaveBeenCalled();
    });
    expect(screen.queryByTestId('tickets-state')).not.toBeInTheDocument();
  });
});
