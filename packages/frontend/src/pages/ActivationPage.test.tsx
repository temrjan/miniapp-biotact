import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import { loyaltyApi, ApiError } from '@/api';
import type { ActivationResult } from '@/types';
import { ActivationPage } from './ActivationPage';

vi.mock('@/api', () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  }
  return { ApiError, loyaltyApi: { consent: vi.fn(), activate: vi.fn() } };
});

const consentMock = vi.mocked(loyaltyApi.consent);
const activateMock = vi.mocked(loyaltyApi.activate);

const activated: ActivationResult = {
  status: 'activated',
  tier: 'newbie',
  pointsAwarded: 750,
  welcomeBonus: 250,
  friendBonus: 0,
  balance: 900,
  lifetimeActivations: 1,
};

function setup(entry = '/activate?code=ABC123') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(client, 'invalidateQueries');
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <ActivationPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
  return { invalidateSpy };
}

describe('ActivationPage', () => {
  beforeEach(() => {
    consentMock.mockReset();
    activateMock.mockReset();
  });

  it('disables the activate button until consent is checked', () => {
    setup();
    const button = screen.getByRole('button', { name: 'Активировать' });
    expect(button).toBeDisabled();

    fireEvent.click(screen.getByRole('checkbox'));
    expect(button).not.toBeDisabled();
  });

  it('records consent then activates and shows the accrual (activated)', async () => {
    consentMock.mockResolvedValue({ consentRecorded: true, joined: true, phoneShared: false });
    activateMock.mockResolvedValue(activated);
    const { invalidateSpy } = setup();

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Активировать' }));

    expect(await screen.findByText(/Начислено 750/)).toBeInTheDocument();
    expect(screen.getByText(/приветственный бонус 250/)).toBeInTheDocument();
    expect(consentMock).toHaveBeenCalledWith('qr');
    expect(activateMock).toHaveBeenCalledWith('ABC123');
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['loyalty', 'me'] })
    );
  });

  it('shows the "Друг" flip message when the tier flips', async () => {
    consentMock.mockResolvedValue({ consentRecorded: true, joined: true, phoneShared: false });
    activateMock.mockResolvedValue({
      status: 'activated',
      tier: 'biotact_friend',
      pointsAwarded: 5800,
      welcomeBonus: 0,
      friendBonus: 5000,
      balance: 5800,
      lifetimeActivations: 10,
    });
    setup();

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Активировать' }));

    expect(await screen.findByText(/Поздравляем/)).toBeInTheDocument();
    // welcomeBonus is 0 on the flip (10th activation, not the first) — no welcome line.
    expect(screen.queryByText(/приветственный бонус/)).not.toBeInTheDocument();
  });

  it('shows a replay message on already_activated WITHOUT claiming a fresh earn', async () => {
    consentMock.mockResolvedValue({ consentRecorded: false, joined: false, phoneShared: false });
    activateMock.mockResolvedValue({ ...activated, status: 'already_activated' });
    setup();

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Активировать' }));

    expect(await screen.findByText('Код уже активирован')).toBeInTheDocument();
    // The historical points echo must NOT be presented as a fresh accrual.
    expect(screen.queryByText(/Начислено/)).not.toBeInTheDocument();
  });

  it('maps a 409 to an invalid-code message with no retry', async () => {
    consentMock.mockResolvedValue({ consentRecorded: true, joined: true, phoneShared: false });
    activateMock.mockRejectedValue(new ApiError('conflict', 409));
    setup();

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Активировать' }));

    expect(await screen.findByText(/Код недействителен или уже был использован/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Повторить' })).not.toBeInTheDocument();
  });

  it('maps a 503 to a try-later message with a retry button', async () => {
    consentMock.mockResolvedValue({ consentRecorded: true, joined: true, phoneShared: false });
    activateMock.mockRejectedValue(new ApiError('unavailable', 503));
    setup();

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Активировать' }));

    expect(await screen.findByText(/Временно недоступно/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument();
  });

  // A 404 from consent = the program is dark (the gate precedes all logic). Distinct from an
  // activate 404 below: this one shows "coming soon", never "invalid code", and skips activate.
  it('maps a consent 404 (dark program) to "coming soon" and does not activate', async () => {
    consentMock.mockRejectedValue(new ApiError('not found', 404));
    setup();

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Активировать' }));

    expect(await screen.findByText(/Программа лояльности скоро будет доступна/)).toBeInTheDocument();
    expect(activateMock).not.toHaveBeenCalled(); // consent gates activate
    expect(screen.queryByRole('button', { name: 'Повторить' })).not.toBeInTheDocument(); // 404 not retriable
  });

  // On a LIVE program, consent succeeds and a 404 comes from activate = the scanned code is unknown.
  // It must read as "invalid code", NOT "coming soon" (the pre-fix bug that conflated both 404s).
  it('maps an activate 404 (unknown code, live) to invalid-code, not "coming soon"', async () => {
    consentMock.mockResolvedValue({ consentRecorded: true, joined: true, phoneShared: false });
    activateMock.mockRejectedValue(new ApiError('not found', 404));
    setup();

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Активировать' }));

    expect(await screen.findByText(/Код недействителен или уже был использован/)).toBeInTheDocument();
    expect(screen.queryByText(/скоро будет доступна/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Повторить' })).not.toBeInTheDocument(); // 404 not retriable
  });

  it('maps a non-ApiError (network/timeout) to a generic message with a retry button', async () => {
    consentMock.mockResolvedValue({ consentRecorded: true, joined: true, phoneShared: false });
    activateMock.mockRejectedValue(new Error('network down'));
    setup();

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Активировать' }));

    expect(await screen.findByText(/Что-то пошло не так/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument();
  });

  it('shows a neutral message when opened without a code', () => {
    setup('/activate');
    expect(screen.getByText(/Ссылка недействительна/)).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });
});
