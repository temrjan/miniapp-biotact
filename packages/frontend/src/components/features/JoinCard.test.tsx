import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { loyaltyApi } from '@/api';
import { JoinCard } from './JoinCard';

vi.mock('@/api', () => ({ loyaltyApi: { consent: vi.fn() } }));

const consentMock = vi.mocked(loyaltyApi.consent);

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(client, 'invalidateQueries');
  render(
    <QueryClientProvider client={client}>
      <JoinCard />
    </QueryClientProvider>
  );
  return { invalidateSpy };
}

describe('JoinCard', () => {
  beforeEach(() => {
    consentMock.mockReset();
  });

  it('disables the join button until consent is checked', () => {
    setup();
    const button = screen.getByRole('button', { name: 'Присоединиться' });
    expect(button).toBeDisabled();

    fireEvent.click(screen.getByRole('checkbox'));
    expect(button).not.toBeDisabled();
  });

  it('joins with source=direct and invalidates the loyalty summary', async () => {
    consentMock.mockResolvedValue({ consentRecorded: true, joined: true, phoneShared: false });
    const { invalidateSpy } = setup();

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Присоединиться' }));

    await waitFor(() => expect(consentMock).toHaveBeenCalledWith('direct'));
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['loyalty', 'me'] })
    );
  });

  it('shows an error and keeps the button on a failed join', async () => {
    consentMock.mockRejectedValue(new Error('boom'));
    setup();

    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.click(screen.getByRole('button', { name: 'Присоединиться' }));

    expect(await screen.findByText(/Что-то пошло не так/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Присоединиться' })).toBeInTheDocument();
  });
});
