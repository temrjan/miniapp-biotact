import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import { loyaltyApi } from '@/api';
import { isLegalEnabled } from '@/legal/flag';
import { buildConsentVersion } from '@/legal';
import { useConsentStore } from '@/stores/consentStore';
import { JoinCard } from './JoinCard';

vi.mock('@/api', () => ({
  loyaltyApi: { consent: vi.fn(), setMarketingConsent: vi.fn() },
}));

const consentMock = vi.mocked(loyaltyApi.consent);
const setMarketingMock = vi.mocked(loyaltyApi.setMarketingConsent);

// The suite runs twice in CI gates: default (flag OFF) and VITE_LEGAL_DOCS=true (ON).
const legalOn = isLegalEnabled();
const expectedDocVersion = legalOn ? buildConsentVersion() : undefined;

// The required consent box is the FIRST checkbox in both modes (OFF renders exactly one).
function tickRequiredConsent() {
  const [required] = screen.getAllByRole('checkbox');
  expect(required, 'required consent checkbox rendered').toBeDefined();
  fireEvent.click(required as HTMLElement);
}

function setup() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(client, 'invalidateQueries');
  render(
    <QueryClientProvider client={client}>
      {/* MemoryRouter: the ON-mode consent labels carry Links to /legal/* */}
      <MemoryRouter>
        <JoinCard />
      </MemoryRouter>
    </QueryClientProvider>
  );
  return { invalidateSpy };
}

describe('JoinCard', () => {
  beforeEach(() => {
    consentMock.mockReset();
    setMarketingMock.mockReset();
    useConsentStore.getState().reset();
    setMarketingMock.mockResolvedValue({ marketingActive: true });
  });

  it('disables the join button until the required consent is checked', () => {
    setup();
    const button = screen.getByRole('button', { name: 'Присоединиться' });
    expect(button).toBeDisabled();

    if (legalOn) {
      // ON: two boxes, neither pre-ticked; the optional marketing box does NOT unlock the button.
      const boxes = screen.getAllByRole('checkbox');
      expect(boxes).toHaveLength(2);
      fireEvent.click(boxes[1] as HTMLElement);
      expect(button).toBeDisabled();
      fireEvent.click(boxes[1] as HTMLElement);
    }

    tickRequiredConsent();
    expect(button).not.toBeDisabled();
  });

  it('joins with source=direct and invalidates the loyalty summary', async () => {
    consentMock.mockResolvedValue({ consentRecorded: true, joined: true, phoneShared: false });
    const { invalidateSpy } = setup();

    tickRequiredConsent();
    fireEvent.click(screen.getByRole('button', { name: 'Присоединиться' }));

    await waitFor(() =>
      expect(consentMock).toHaveBeenCalledWith('direct', expectedDocVersion)
    );
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['loyalty', 'me'] })
    );
  });

  it('records a ticked marketing box as a grant after consent (ON)', async () => {
    consentMock.mockResolvedValue({ consentRecorded: true, joined: true, phoneShared: false });
    setup();

    if (legalOn) {
      const boxes = screen.getAllByRole('checkbox');
      fireEvent.click(boxes[1] as HTMLElement);
    }
    tickRequiredConsent();
    fireEvent.click(screen.getByRole('button', { name: 'Присоединиться' }));

    await waitFor(() => expect(consentMock).toHaveBeenCalled());
    if (legalOn) {
      expect(setMarketingMock).toHaveBeenCalledWith(true, expectedDocVersion);
      const [consentOrder] = consentMock.mock.invocationCallOrder;
      const [marketingOrder] = setMarketingMock.mock.invocationCallOrder;
      expect(marketingOrder).toBeGreaterThan(consentOrder ?? -1);
    } else {
      expect(setMarketingMock).not.toHaveBeenCalled();
    }
  });

  it('shows an error and keeps the button on a failed join', async () => {
    consentMock.mockRejectedValue(new Error('boom'));
    setup();

    tickRequiredConsent();
    fireEvent.click(screen.getByRole('button', { name: 'Присоединиться' }));

    expect(await screen.findByText(/Что-то пошло не так/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Присоединиться' })).toBeInTheDocument();
  });
});
