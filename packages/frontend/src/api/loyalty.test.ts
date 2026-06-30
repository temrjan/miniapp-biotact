import { describe, it, expect, vi, beforeEach } from 'vitest';

import { api } from './client';
import { loyaltyApi } from './loyalty';

vi.mock('./client', () => ({
  api: { get: vi.fn() },
}));

const getMock = vi.mocked(api.get);

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
