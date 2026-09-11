import { describe, it, expect, vi, beforeEach } from 'vitest';

import { api } from './client';
import { loyaltyApi } from './loyalty';

vi.mock('./client', () => ({
  api: { get: vi.fn(), post: vi.fn() },
}));

const getMock = vi.mocked(api.get);
const postMock = vi.mocked(api.post);

describe('loyaltyApi.getMe', () => {
  beforeEach(() => {
    getMock.mockReset();
  });

  it('maps the snake_case DTO to the camelCase domain shape (no crossed fields)', async () => {
    getMock.mockResolvedValue({
      tier: 'newbie',
      balance: 5800,
      available: 2400,
      reserved_until: '2026-07-23T09:30:00+00:00',
      lifetime_activations: 3,
      activations_to_friend: 7,
      is_member: true,
      gender: 'male',
      age: 47,
      city: 'Ташкент',
      is_adult: true,
      // Distinct true/false catches a crossed consents mapping that would still typecheck.
      consents: { data_processing: true, marketing: false },
    });

    const result = await loyaltyApi.getMe();

    // Distinct values (3 vs 7, 5800 vs 2400) catch a crossed mapping that would still typecheck.
    expect(result).toEqual({
      tier: 'newbie',
      balance: 5800,
      available: 2400,
      reservedUntil: new Date('2026-07-23T09:30:00+00:00'),
      lifetimeActivations: 3,
      activationsToFriend: 7,
      isMember: true,
      gender: 'male',
      age: 47,
      city: 'Ташкент',
      isAdult: true,
      consents: { dataProcessing: true, marketing: false },
    });
    expect(getMock).toHaveBeenCalledWith('/loyalty/me');
  });

  it('maps survey fields absent from an older API response (deploy window) to null', async () => {
    // An older API omits the survey keys entirely — they must land as null, not undefined.
    getMock.mockResolvedValue({
      tier: 'newbie',
      balance: 0,
      lifetime_activations: 0,
      activations_to_friend: 10,
      is_member: true,
    });

    const result = await loyaltyApi.getMe();

    expect(result.gender).toBeNull();
    expect(result.age).toBeNull();
    expect(result.city).toBeNull();
    // `is_adult` is a bool, not a nullable answer: absent degrades to "not confirmed", which asks
    // the member once more rather than displaying a confirmation they never gave.
    expect(result.isAdult).toBe(false);
    // Same rule for `consents`: false/false re-shows the consent gate rather than hiding it.
    expect(result.consents).toEqual({ dataProcessing: false, marketing: false });
  });

  it('falls back to the full balance when an older API omits `available` (deploy window)', async () => {
    // A NON-ZERO balance is what makes this test honest: with balance 0 it could not tell
    // `?? balance` apart from `?? 0`. Falling back to 0 would show a customer WITH points that
    // they have none — the exact lie `available` exists to remove; falling back to the balance
    // degrades to the pre-`available` behaviour, where the server's 409 is the backstop.
    getMock.mockResolvedValue({
      tier: 'newbie',
      balance: 5800,
      lifetime_activations: 3,
      activations_to_friend: 7,
      is_member: true,
    });

    const result = await loyaltyApi.getMe();

    expect(result.available).toBe(5800);
    expect(result.reservedUntil).toBeNull();
  });

  it('maps an unparseable reserved_until to null instead of an Invalid Date', async () => {
    getMock.mockResolvedValue({
      tier: 'newbie',
      balance: 100,
      available: 100,
      reserved_until: 'not-a-timestamp',
      lifetime_activations: 0,
      activations_to_friend: 10,
      is_member: true,
    });

    const result = await loyaltyApi.getMe();

    // An Invalid Date would render as "Invalid Date" in the reserve notice.
    expect(result.reservedUntil).toBeNull();
  });
});

describe('loyaltyApi.consent', () => {
  beforeEach(() => {
    postMock.mockReset();
  });

  it('posts the source WITHOUT a version key when none is given (flag-OFF shape, M2)', async () => {
    postMock.mockResolvedValue({ consent_recorded: true, joined: true, phone_shared: false });

    const result = await loyaltyApi.consent('qr');

    // The key must stay absent, not `undefined` — a present-but-empty key would not be the
    // pre-PR-2 request the server's "1.0" default is built around.
    expect(postMock).toHaveBeenCalledWith('/loyalty/consent', { source: 'qr' });
    expect(result).toEqual({ consentRecorded: true, joined: true, phoneShared: false });
  });

  it('posts doc_version verbatim when the caller passes one (flag-ON shape)', async () => {
    postMock.mockResolvedValue({ consent_recorded: true, joined: true, phone_shared: false });

    await loyaltyApi.consent('direct', 'offer:draft-2026-08-20+policy:draft-2026-08-20');

    expect(postMock).toHaveBeenCalledWith('/loyalty/consent', {
      source: 'direct',
      doc_version: 'offer:draft-2026-08-20+policy:draft-2026-08-20',
    });
  });
});

describe('loyaltyApi.setMarketingConsent', () => {
  beforeEach(() => {
    postMock.mockReset();
  });

  it('posts granted and maps marketing_active to camelCase', async () => {
    postMock.mockResolvedValue({ marketing_active: true });

    const result = await loyaltyApi.setMarketingConsent(true);

    expect(postMock).toHaveBeenCalledWith('/loyalty/consent/marketing', { granted: true });
    expect(result).toEqual({ marketingActive: true });
  });

  it('sends granted false on revoke (false is a value, not an absence)', async () => {
    postMock.mockResolvedValue({ marketing_active: false });

    const result = await loyaltyApi.setMarketingConsent(false);

    expect(postMock).toHaveBeenCalledWith('/loyalty/consent/marketing', { granted: false });
    expect(result).toEqual({ marketingActive: false });
  });

  it('includes doc_version only when given (same rule as consent)', async () => {
    postMock.mockResolvedValue({ marketing_active: true });

    await loyaltyApi.setMarketingConsent(true, 'offer:draft-2026-08-20+policy:draft-2026-08-20');

    expect(postMock).toHaveBeenCalledWith('/loyalty/consent/marketing', {
      granted: true,
      doc_version: 'offer:draft-2026-08-20+policy:draft-2026-08-20',
    });
  });
});

describe('loyaltyApi.activate', () => {
  beforeEach(() => {
    postMock.mockReset();
  });

  it('posts the code and maps the snake_case DTO (distinct values catch a crossed mapping)', async () => {
    postMock.mockResolvedValue({
      status: 'activated',
      tier: 'newbie',
      points_awarded: 750,
      welcome_bonus: 250,
      friend_bonus: 0,
      balance: 900,
      lifetime_activations: 1,
    });

    const result = await loyaltyApi.activate('ABC123');

    expect(postMock).toHaveBeenCalledWith('/qr/activate', { code: 'ABC123' });
    expect(result).toEqual({
      status: 'activated',
      tier: 'newbie',
      pointsAwarded: 750,
      welcomeBonus: 250,
      friendBonus: 0,
      balance: 900,
      lifetimeActivations: 1,
    });
  });
});

describe('loyaltyApi.survey', () => {
  beforeEach(() => {
    postMock.mockReset();
  });

  it('posts the payload as-is and maps the response to camelCase', async () => {
    postMock.mockResolvedValue({ updated_fields: ['age', 'city'], survey_done: true });

    const result = await loyaltyApi.survey({ age: 30, city: 'Tashkent' });

    expect(postMock).toHaveBeenCalledWith('/loyalty/survey', { age: 30, city: 'Tashkent' });
    expect(result).toEqual({ updatedFields: ['age', 'city'], surveyDone: true });
  });

  it('renames isAdult to the wire key is_adult', async () => {
    postMock.mockResolvedValue({ updated_fields: ['is_adult'], survey_done: false });

    await loyaltyApi.survey({ isAdult: true });

    // The server field is `is_adult`; a camelCase key would be ignored and 422 the empty body.
    expect(postMock).toHaveBeenCalledWith('/loyalty/survey', { is_adult: true });
  });

  it('sends is_adult false when a stored confirmation is withdrawn', async () => {
    postMock.mockResolvedValue({ updated_fields: ['is_adult'], survey_done: false });

    await loyaltyApi.survey({ isAdult: false, city: 'Tashkent' });

    // `false` is a value: dropping it as falsy would leave the member confirmed against their edit.
    expect(postMock).toHaveBeenCalledWith('/loyalty/survey', { city: 'Tashkent', is_adult: false });
  });

  it('omits the is_adult key entirely when the checkbox was not touched', async () => {
    postMock.mockResolvedValue({ updated_fields: ['city'], survey_done: true });

    await loyaltyApi.survey({ city: 'Tashkent' });

    // A present `is_adult` would overwrite the stored confirmation on every unrelated edit.
    expect(postMock).toHaveBeenCalledWith('/loyalty/survey', { city: 'Tashkent' });
  });
});

describe('loyaltyApi.getMyTickets', () => {
  beforeEach(() => {
    getMock.mockReset();
  });

  it('maps the snake_case DTO to the camelCase domain shape (no crossed fields)', async () => {
    getMock.mockResolvedValue({
      tickets: 1,
      is_frozen: false,
      draw_number: null,
      prize_label: null,
      // Distinct numbers (1 vs 2, rank 3) catch a crossed mapping that would still typecheck.
      result: {
        draw_number: 4,
        tickets: 2,
        won: true,
        prize_rank: 3,
        prize_label: 'Аэрогриль',
      },
      numbers: [7, 12],
      clean_tickets: 1,
      not_playing_reason: 'survey',
    });

    const result = await loyaltyApi.getMyTickets();

    expect(result).toEqual({
      tickets: 1,
      isFrozen: false,
      drawNumber: null,
      prizeLabel: null,
      result: {
        drawNumber: 4,
        tickets: 2,
        won: true,
        prizeRank: 3,
        prizeLabel: 'Аэрогриль',
      },
      numbers: [7, 12],
      cleanTickets: 1,
      notPlayingReason: 'survey',
    });
  });

  it('maps a frozen draw without a result block', async () => {
    getMock.mockResolvedValue({
      tickets: 2,
      is_frozen: true,
      draw_number: 3,
      prize_label: 'Аэрогриль',
      result: null,
      numbers: [7, 12],
      clean_tickets: 0,
      not_playing_reason: null,
    });

    expect(await loyaltyApi.getMyTickets()).toEqual({
      tickets: 2,
      isFrozen: true,
      drawNumber: 3,
      prizeLabel: 'Аэрогриль',
      result: null,
      numbers: [7, 12],
      cleanTickets: 0,
      notPlayingReason: null,
    });
  });

  it('never leaks undefined when an older API omits the optional keys', async () => {
    // Deploy window: the frontend ships before the API. The domain type promises `| null`, so an
    // absent key must arrive as null — `undefined` would slip past every `=== null` check
    // downstream and render as a missing field instead of an empty one.
    getMock.mockResolvedValue({ tickets: 0 });

    const result = await loyaltyApi.getMyTickets();

    expect(result.isFrozen).toBe(false); // safest default: a live recount, never a fixed promise
    expect(result.drawNumber).toBeNull();
    expect(result.prizeLabel).toBeNull();
    expect(result.result).toBeNull();
    // The step-5 fields follow the same rule, and their defaults show LESS than the member has
    // rather than something untrue: no numbers to check yet, nothing in hand, no reason to explain.
    expect(result.numbers).toEqual([]);
    expect(result.cleanTickets).toBe(0);
    expect(result.notPlayingReason).toBeNull();
    expect(Object.values(result)).not.toContain(undefined);
  });
});
