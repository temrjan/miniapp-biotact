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
      lifetime_activations: 3,
      activations_to_friend: 7,
    });

    const result = await loyaltyApi.getMe();

    // Distinct values (3 vs 7) catch a crossed mapping that would still typecheck.
    expect(result).toEqual({
      tier: 'newbie',
      balance: 5800,
      lifetimeActivations: 3,
      activationsToFriend: 7,
    });
    expect(getMock).toHaveBeenCalledWith('/loyalty/me');
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
