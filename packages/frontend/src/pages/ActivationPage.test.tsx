import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

import { loyaltyApi, ApiError } from '@/api';
import { isLegalEnabled } from '@/legal/flag';
import { buildConsentVersion } from '@/legal';
import { useConsentStore } from '@/stores/consentStore';
import type { ActivationResult, LoyaltySummary } from '@/types';
import { ActivationPage } from './ActivationPage';

vi.mock('@/api', () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  }
  return {
    ApiError,
    loyaltyApi: {
      consent: vi.fn(),
      activate: vi.fn(),
      getMe: vi.fn(),
      setMarketingConsent: vi.fn(),
    },
  };
});

const consentMock = vi.mocked(loyaltyApi.consent);
const activateMock = vi.mocked(loyaltyApi.activate);
const getMeMock = vi.mocked(loyaltyApi.getMe);
const setMarketingMock = vi.mocked(loyaltyApi.setMarketingConsent);

// The suite runs twice in CI gates: default (flag OFF) and VITE_LEGAL_DOCS=true (ON).
// OFF: одиночный чекбокс и consent без версии; ON: два чекбокса, версия из реестра, гейт по /me.
const legalOn = isLegalEnabled();
const expectedDocVersion = legalOn ? buildConsentVersion() : undefined;

const activated: ActivationResult = {
  status: 'activated',
  tier: 'newbie',
  pointsAwarded: 750,
  welcomeBonus: 250,
  friendBonus: 0,
  balance: 900,
  lifetimeActivations: 1,
};

const freshSummary: LoyaltySummary = {
  tier: 'newbie',
  balance: 0,
  available: 0,
  reservedUntil: null,
  lifetimeActivations: 0,
  activationsToFriend: 10,
  isMember: false,
  gender: null,
  age: null,
  city: null,
  isAdult: false,
  consents: { dataProcessing: false, marketing: false },
};

// The required consent box is the FIRST of the checkboxes in both modes (OFF renders exactly one).
function tickRequiredConsent() {
  const [required] = screen.getAllByRole('checkbox');
  expect(required, 'required consent checkbox rendered').toBeDefined();
  fireEvent.click(required as HTMLElement);
}

function renderPage(entry = '/activate?code=ABC123') {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <ActivationPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

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
    getMeMock.mockReset();
    setMarketingMock.mockReset();
    useConsentStore.getState().reset();
    // Default /me: no active grants — the consent gate shows (ON). OFF never calls it.
    getMeMock.mockResolvedValue(freshSummary);
    setMarketingMock.mockResolvedValue({ marketingActive: true });
  });

  it('disables the activate button until the required consent is checked', () => {
    setup();
    const button = screen.getByRole('button', { name: 'Активировать' });
    expect(button).toBeDisabled();

    if (legalOn) {
      // ON: two boxes, neither pre-ticked; the optional marketing box does NOT unlock the button.
      const boxes = screen.getAllByRole('checkbox');
      expect(boxes).toHaveLength(2);
      fireEvent.click(boxes[1] as HTMLElement); // marketing
      expect(button).toBeDisabled();
      fireEvent.click(boxes[1] as HTMLElement); // untick back
    }

    tickRequiredConsent();
    expect(button).not.toBeDisabled();
  });

  it('links the required consent label to the offer and policy documents (ON only)', () => {
    setup();
    if (!legalOn) {
      // OFF: the legacy single-sentence label, no document links.
      expect(screen.queryByRole('link')).not.toBeInTheDocument();
      return;
    }
    const links = screen.getAllByRole('link');
    const hrefs = links.map((link) => link.getAttribute('href'));
    expect(hrefs).toContain('/legal/offer');
    expect(hrefs).toContain('/legal/policy');
  });

  it('a member with an active grant sees no checkboxes and activates straight away (ON)', async () => {
    getMeMock.mockResolvedValue({
      ...freshSummary,
      isMember: true,
      consents: { dataProcessing: true, marketing: false },
    });
    consentMock.mockResolvedValue({ consentRecorded: false, joined: false, phoneShared: false });
    activateMock.mockResolvedValue(activated);
    setup();

    if (!legalOn) {
      // OFF has no /me gate at all — the single legacy checkbox is always shown.
      expect(screen.getAllByRole('checkbox')).toHaveLength(1);
      return;
    }

    // /me resolves asynchronously — wait until the gate swaps checkboxes for the bare button.
    await waitFor(() => expect(screen.queryByRole('checkbox')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Активировать' })).not.toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Активировать' }));
    expect(await screen.findByText(/Начислено 750/)).toBeInTheDocument();
    expect(consentMock).toHaveBeenCalledWith('qr', expectedDocVersion);
    // No marketing box was offered — no grant call either.
    expect(setMarketingMock).not.toHaveBeenCalled();
  });

  it('records a ticked marketing box as a grant after consent (ON)', async () => {
    consentMock.mockResolvedValue({ consentRecorded: true, joined: true, phoneShared: false });
    activateMock.mockResolvedValue(activated);
    setup();

    if (legalOn) {
      const boxes = screen.getAllByRole('checkbox');
      fireEvent.click(boxes[1] as HTMLElement); // marketing — optional, does not gate
    }
    tickRequiredConsent();
    fireEvent.click(screen.getByRole('button', { name: 'Активировать' }));

    expect(await screen.findByText(/Начислено 750/)).toBeInTheDocument();
    if (legalOn) {
      expect(setMarketingMock).toHaveBeenCalledWith(true, expectedDocVersion);
      // The grant runs AFTER consent: it is member-only, consent creates the membership.
      const [consentOrder] = consentMock.mock.invocationCallOrder;
      const [marketingOrder] = setMarketingMock.mock.invocationCallOrder;
      expect(marketingOrder).toBeGreaterThan(consentOrder ?? -1);
    } else {
      // OFF: no marketing surface at all.
      expect(setMarketingMock).not.toHaveBeenCalled();
    }
  });

  it('records consent then activates and shows the accrual (activated)', async () => {
    consentMock.mockResolvedValue({ consentRecorded: true, joined: true, phoneShared: false });
    activateMock.mockResolvedValue(activated);
    const { invalidateSpy } = setup();

    tickRequiredConsent();
    fireEvent.click(screen.getByRole('button', { name: 'Активировать' }));

    expect(await screen.findByText(/Начислено 750/)).toBeInTheDocument();
    expect(screen.getByText(/приветственный бонус 250/)).toBeInTheDocument();
    expect(consentMock).toHaveBeenCalledWith('qr', expectedDocVersion);
    expect(activateMock).toHaveBeenCalledWith('ABC123');
    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['loyalty', 'me'] })
    );
  });

  it('keeps the checkbox state when the store survives a remount (ON)', () => {
    if (!legalOn) {
      return; // OFF state is local and intentionally lost on remount (pre-PR-2 behaviour)
    }
    const first = renderPage();
    tickRequiredConsent();
    // Leaving for a legal document unmounts the page; the store must hold the tick on return.
    first.unmount();
    renderPage();
    const [required] = screen.getAllByRole('checkbox');
    expect(required).toBeChecked();
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

    tickRequiredConsent();
    fireEvent.click(screen.getByRole('button', { name: 'Активировать' }));

    expect(await screen.findByText(/Поздравляем/)).toBeInTheDocument();
    // welcomeBonus is 0 on the flip (10th activation, not the first) — no welcome line.
    expect(screen.queryByText(/приветственный бонус/)).not.toBeInTheDocument();
  });

  it('shows a replay message on already_activated WITHOUT claiming a fresh earn', async () => {
    consentMock.mockResolvedValue({ consentRecorded: false, joined: false, phoneShared: false });
    activateMock.mockResolvedValue({ ...activated, status: 'already_activated' });
    setup();

    tickRequiredConsent();
    fireEvent.click(screen.getByRole('button', { name: 'Активировать' }));

    expect(await screen.findByText('Код уже активирован')).toBeInTheDocument();
    // The historical points echo must NOT be presented as a fresh accrual.
    expect(screen.queryByText(/Начислено/)).not.toBeInTheDocument();
  });

  it('maps a 409 to an invalid-code message with no retry', async () => {
    consentMock.mockResolvedValue({ consentRecorded: true, joined: true, phoneShared: false });
    activateMock.mockRejectedValue(new ApiError('conflict', 409));
    setup();

    tickRequiredConsent();
    fireEvent.click(screen.getByRole('button', { name: 'Активировать' }));

    expect(await screen.findByText(/Код недействителен или уже был использован/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Повторить' })).not.toBeInTheDocument();
  });

  it('maps a 503 to a try-later message with a retry button', async () => {
    consentMock.mockResolvedValue({ consentRecorded: true, joined: true, phoneShared: false });
    activateMock.mockRejectedValue(new ApiError('unavailable', 503));
    setup();

    tickRequiredConsent();
    fireEvent.click(screen.getByRole('button', { name: 'Активировать' }));

    expect(await screen.findByText(/Временно недоступно/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeInTheDocument();
  });

  // A 404 from consent = the program is dark (the gate precedes all logic). Distinct from an
  // activate 404 below: this one shows "coming soon", never "invalid code", and skips activate.
  it('maps a consent 404 (dark program) to "coming soon" and does not activate', async () => {
    consentMock.mockRejectedValue(new ApiError('not found', 404));
    setup();

    tickRequiredConsent();
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

    tickRequiredConsent();
    fireEvent.click(screen.getByRole('button', { name: 'Активировать' }));

    expect(await screen.findByText(/Код недействителен или уже был использован/)).toBeInTheDocument();
    expect(screen.queryByText(/скоро будет доступна/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Повторить' })).not.toBeInTheDocument(); // 404 not retriable
  });

  it('maps a non-ApiError (network/timeout) to a generic message with a retry button', async () => {
    consentMock.mockResolvedValue({ consentRecorded: true, joined: true, phoneShared: false });
    activateMock.mockRejectedValue(new Error('network down'));
    setup();

    tickRequiredConsent();
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
