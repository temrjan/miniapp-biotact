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

const base = { numbers: [] as number[], cleanTickets: 0, notPlayingReason: null };

const prospective: MyTickets = {
  ...base,
  tickets: 2,
  isFrozen: false,
  period: null,
  prizeLabel: null,
  result: null,
  cleanTickets: 2,
};

const frozen: MyTickets = {
  ...base,
  tickets: 2,
  isFrozen: true,
  period: '№3',
  prizeLabel: 'Аэрогриль',
  result: null,
  numbers: [7, 12],
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
    // The wording changed with the model: nothing expires on its own any more, so the line no
    // longer warns that the number may shrink — it says what these tickets are waiting for.
    expect(screen.getByTestId('tickets-state').textContent).toMatch(/ближайшем розыгрыше/);
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

describe('TicketsCard — step 5: numbers, tickets in hand, and why they will not play', () => {
  beforeEach(() => {
    getMyTicketsMock.mockReset();
  });

  it('shows the member their own ticket numbers once a draw has stamped them', async () => {
    getMyTicketsMock.mockResolvedValue(frozen);
    renderCard(<TicketsCard />);

    const numbers = await screen.findByTestId('tickets-numbers');
    expect(numbers.textContent).toContain('7, 12');
  });

  it('keeps showing the numbers after the draw has been drawn', async () => {
    // This is when the published list appears. A member without their numbers cannot find
    // themselves in it, which is the entire reason the numbers are shown at all.
    getMyTicketsMock.mockResolvedValue({
      ...base,
      tickets: 0,
      isFrozen: false,
      period: null,
      prizeLabel: null,
      numbers: [7, 12],
      result: { period: '№3', tickets: 2, won: false, prizeRank: null, prizeLabel: null },
    });
    renderCard(<TicketsCard />);

    const numbers = await screen.findByTestId('tickets-numbers');
    expect(numbers.textContent).toContain('7, 12');
    expect(screen.getByTestId('tickets-result')).toBeInTheDocument();
  });

  it('names the reason instead of promising the next draw when a ticket will not play', async () => {
    // The ratified condition. "One more waits for the next draw" would be a lie to someone whose
    // survey is incomplete: without it the ticket will not play then either.
    getMyTicketsMock.mockResolvedValue({
      ...frozen,
      cleanTickets: 1,
      notPlayingReason: 'survey',
    });
    renderCard(<TicketsCard />);

    const waiting = await screen.findByTestId('tickets-waiting');
    expect(waiting.textContent).toMatch(/анкету/);
    expect(waiting.textContent).not.toMatch(/ждёт следующего/);
  });

  it('promises the next draw when the held ticket will actually play', async () => {
    getMyTicketsMock.mockResolvedValue({ ...frozen, cleanTickets: 1, notPlayingReason: null });
    renderCard(<TicketsCard />);

    const waiting = await screen.findByTestId('tickets-waiting');
    expect(waiting.textContent).toMatch(/ждёт следующего розыгрыша/);
    expect(waiting.textContent).toContain('1 билет');
  });

  it('explains the 18+ block by its own sentence, not the survey one', async () => {
    getMyTicketsMock.mockResolvedValue({ ...prospective, notPlayingReason: 'age' });
    renderCard(<TicketsCard />);

    const blocked = await screen.findByTestId('tickets-blocked');
    expect(blocked.textContent).toMatch(/18/);
  });

  it('degrades an unknown reason code to the general sentence', async () => {
    // The server may grow a third predicate before this bundle is updated. Printing the raw code
    // or nothing at all would both be worse than a general, true sentence.
    getMyTicketsMock.mockResolvedValue({ ...prospective, notPlayingReason: 'something_new' });
    renderCard(<TicketsCard />);

    const blocked = await screen.findByTestId('tickets-blocked');
    expect(blocked.textContent).toMatch(/пока не участвует/);
  });

  it('agrees the noun with the number', async () => {
    // "1 билетов" is what the card said before this step, for every value the ceiling allows.
    getMyTicketsMock.mockResolvedValue({ ...prospective, tickets: 1, cleanTickets: 1 });
    const { unmount } = renderCard(<TicketsCard />);
    expect((await screen.findByTestId('tickets-unit')).textContent).toBe('билет');
    unmount();

    getMyTicketsMock.mockResolvedValue({ ...prospective, tickets: 2, cleanTickets: 2 });
    const second = renderCard(<TicketsCard />);
    expect((await screen.findByTestId('tickets-unit')).textContent).toBe('билета');
    second.unmount();

    getMyTicketsMock.mockResolvedValue({ ...prospective, tickets: 5, cleanTickets: 5 });
    renderCard(<TicketsCard />);
    expect((await screen.findByTestId('tickets-unit')).textContent).toBe('билетов');
  });
});
