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

  it('posts the source and maps the response to camelCase', async () => {
    postMock.mockResolvedValue({ consent_recorded: true, joined: true, phone_shared: false });

    const result = await loyaltyApi.consent('qr');

    expect(postMock).toHaveBeenCalledWith('/loyalty/consent', { source: 'qr' });
    expect(result).toEqual({ consentRecorded: true, joined: true, phoneShared: false });
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
});
