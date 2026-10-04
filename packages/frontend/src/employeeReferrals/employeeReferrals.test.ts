import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ReferralContext, REFERRAL_KEY, canonicalProduct, canonicalToken } from './context';

const A = 'AAAAAAAAAAAAAAAAAAAAAA';
const B = 'AQEBAQEBAQEBAQEBAQEBAQ';
const start = Date.parse('2026-10-04T12:00:00.000000Z');
const valid = (product = '39') => ({
  state: 'valid',
  product_id: product,
  observed_at: '2026-10-04T12:00:00.000000Z',
  expires_at: '2026-10-04T13:00:00.000000Z',
});
function deferred() {
  let finish: (value: unknown) => void = () => {};
  const promise = new Promise<unknown>((resolve) => {
    finish = resolve;
  });
  return { promise, finish };
}
function harness() {
  let mono = 0,
    wall = start,
    identity = 'synthetic-auth';
  const resolve = vi.fn<(token: string) => Promise<unknown>>();
  const storage = {
    getItem: vi.fn((key: string) => localStorage.getItem(key)),
    setItem: vi.fn((key: string, value: string) => localStorage.setItem(key, value)),
    removeItem: vi.fn((key: string) => localStorage.removeItem(key)),
  };
  const context = new ReferralContext({
    enabled: () => true,
    resolve,
    storage: () => storage,
    monotonic: () => mono,
    wall: () => wall,
    identity: () => identity,
  });
  return {
    context,
    resolve,
    storage,
    clock: (m: number, w = start) => {
      mono = m;
      wall = w;
    },
    switchIdentity: () => {
      identity = 'other';
    },
  };
}
beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
});
afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
});

describe('single current document referral', () => {
  it('canonical bounds and final base64 bits preserve BIGINT as a string', () => {
    expect(canonicalProduct('9223372036854775807')).toBe(true);
    for (const x of ['9223372036854775808', '039', '0', '1e3'])
      expect(canonicalProduct(x)).toBe(false);
    expect(canonicalToken('_____________________w')).toBe(true);
    for (const x of ['A'.repeat(21) + 'B', 'A'.repeat(23), '%41'.repeat(22), ''])
      expect(canonicalToken(x)).toBe(false);
  });
  it('new pending entry clears old immediately; stale A success cannot restore or redirect', async () => {
    const h = harness(),
      slow = deferred(),
      redirect = vi.fn();
    h.resolve.mockResolvedValueOnce(valid());
    await h.context.enter([A]);
    expect(h.context.tokenForOrder()).toBe(A);
    h.resolve.mockReturnValueOnce(slow.promise);
    const pending = h.context.enter([A], redirect);
    expect(h.context.tokenForOrder()).toBeUndefined();
    expect(localStorage.getItem(REFERRAL_KEY)).toBeNull();
    h.resolve.mockResolvedValueOnce(valid('40'));
    await h.context.enter([B]);
    slow.finish(valid());
    await pending;
    expect(h.context.tokenForOrder()).toBe(B);
    expect(redirect).not.toHaveBeenCalled();
  });
  it('A → B → A uses generations, old negative does not clear current A', async () => {
    const h = harness(),
      slow = deferred();
    h.resolve.mockReturnValueOnce(slow.promise);
    const first = h.context.enter([A]);
    h.resolve.mockResolvedValueOnce(valid());
    await h.context.enter([B]);
    h.resolve.mockResolvedValueOnce(valid());
    await h.context.enter([A]);
    slow.finish({ state: 'invalid' });
    await first;
    expect(h.context.tokenForOrder()).toBe(A);
  });
  it.each([{ entry: [] }, { entry: [''] }, { entry: ['bad'] }, { entry: [A, B] }])(
    'invalid entry clears rather than reviving storage %j',
    async ({ entry }) => {
      const h = harness();
      h.resolve.mockResolvedValue(valid());
      await h.context.enter([A]);
      await h.context.enter(entry);
      expect(h.context.tokenForOrder()).toBeUndefined();
      expect(localStorage.getItem(REFERRAL_KEY)).toBeNull();
    }
  );
  it.each([
    { state: 'invalid' },
    { ...valid(), product_id: 39 },
    { ...valid(), extra: 'private' },
    { ...valid(), expires_at: '2026-02-30T00:00:00.000000Z' },
  ])('rejects invalid DTO %j', async (response) => {
    const h = harness();
    h.resolve.mockResolvedValue(response);
    await h.context.enter([A]);
    expect(h.context.tokenForOrder()).toBeUndefined();
  });
  it('failure is unavailable with no retired token, identity switch invalidates pending', async () => {
    const h = harness(),
      slow = deferred();
    h.resolve.mockRejectedValueOnce(new Error('private'));
    await h.context.enter([A]);
    expect(h.context.snapshot()).toBe('unavailable');
    expect(h.context.tokenForOrder()).toBeUndefined();
    h.resolve.mockReturnValueOnce(slow.promise);
    const pending = h.context.enter([B]);
    h.switchIdentity();
    slow.finish(valid());
    await pending;
    expect(h.context.snapshot()).toBe('empty');
    expect(h.context.tokenForOrder()).toBeUndefined();
  });
  it('reload is only a candidate, focus+visibility coalesce and never redirect', async () => {
    localStorage.setItem(
      REFERRAL_KEY,
      JSON.stringify({ version: 1, token: A, expires_at: valid().expires_at })
    );
    const h = harness(),
      slow = deferred();
    h.resolve.mockReturnValueOnce(slow.promise);
    const first = h.context.reopen();
    await h.context.reopen();
    expect(h.resolve).toHaveBeenCalledTimes(1);
    expect(h.context.tokenForOrder()).toBeUndefined();
    slow.finish(valid());
    await first;
    expect(h.context.tokenForOrder()).toBe(A);
  });
  it('monotonic end subtracts RTT and prePOST does not depend on timer or backward wall clock', async () => {
    const h = harness(),
      slow = deferred();
    h.resolve.mockReturnValueOnce(slow.promise);
    const pending = h.context.enter([A]);
    h.clock(1000);
    slow.finish(valid());
    await pending;
    h.clock(3600000, start - 1000000);
    expect(h.context.tokenForOrder()).toBeUndefined();
    expect(localStorage.getItem(REFERRAL_KEY)).toBeNull();
  });
  it.each(['getItem', 'setItem', 'removeItem'] as const)(
    'storage %s failure makes the whole document memory only',
    async (operation) => {
      const h = harness();
      localStorage.setItem(
        REFERRAL_KEY,
        JSON.stringify({ version: 1, token: A, expires_at: valid().expires_at })
      );
      h.storage[operation].mockImplementationOnce(() => {
        throw new Error('storage blocked');
      });
      if (operation === 'getItem') h.context.tokenForOrder();
      h.resolve.mockResolvedValue(valid());
      await h.context.enter([B]);
      // Trigger get failure with active context if it was not called by entry.
      expect(h.context.tokenForOrder()).toBe(B);
      localStorage.setItem(
        REFERRAL_KEY,
        JSON.stringify({ version: 1, token: A, expires_at: valid().expires_at })
      );
      await h.context.storageChanged();
      expect(h.context.tokenForOrder()).toBe(B);
      await h.context.reopen();
      expect(h.resolve).toHaveBeenLastCalledWith(B);
      expect(h.context.tokenForOrder()).toBe(B);
    }
  );
  it('shared record change suppresses old POST token and requires resolve of observed record', async () => {
    const h = harness(),
      slow = deferred();
    h.resolve.mockResolvedValueOnce(valid());
    await h.context.enter([A]);
    localStorage.setItem(
      REFERRAL_KEY,
      JSON.stringify({ version: 1, token: B, expires_at: valid().expires_at })
    );
    h.resolve.mockReturnValueOnce(slow.promise);
    expect(h.context.tokenForOrder()).toBeUndefined();
    expect(h.resolve).toHaveBeenLastCalledWith(B);
    slow.finish(valid());
    await slow.promise;
    await Promise.resolve();
    expect(h.context.tokenForOrder()).toBe(B);
  });
  it('OFF removes memory/disk and does not resolve', async () => {
    localStorage.setItem(REFERRAL_KEY, 'stale');
    const resolve = vi.fn();
    const context = new ReferralContext({
      enabled: () => false,
      resolve,
      storage: () => localStorage,
      monotonic: () => 0,
      wall: () => start,
      identity: () => '',
    });
    await context.enter([A]);
    await context.reopen();
    expect(resolve).not.toHaveBeenCalled();
    expect(localStorage.getItem(REFERRAL_KEY)).toBeNull();
  });
});
