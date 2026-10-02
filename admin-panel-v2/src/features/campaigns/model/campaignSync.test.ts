import { describe, expect, it } from 'vitest'
import { decodeSync, parseSyncJSON, remainingMs } from './campaignSync'
import { syncFixture } from '../test/syncFixture'

describe('strict sync DTO', () => {
  it('preserves bigint strings and rejects wrong identity', () => {
    expect(decodeSync(syncFixture(), 1).confirmed_revision).toBe('9223372036854775807')
    expect(() => decodeSync(syncFixture(), 2)).toThrow()
  })
  it.each([
    { campaign_id: '01' }, { desired_revision: '9223372036854775808' }, { confirmed_revision: 1 },
    { sender_enabled: 1 }, { participant_count: 9007199254740992 }, { pending_count: true }, { blocked_count: 2 },
    { observed_at: '2026-02-30T00:00:00.000000Z' }, { valid_until: '2026-01-01T00:00:00Z' },
    { observed_at: '2026-10-02T00:00:00.000000+00:00' }, { token: 'PRIVATE_TOKEN' }, { last_error_code: 'private' },
  ])('rejects malformed/private fields %j', change => {
    expect(() => decodeSync({ ...syncFixture(), ...change }, 1)).toThrow()
  })
  it('rejects missing fields and duplicate decoded keys on the raw JSON boundary', () => {
    expect(() => decodeSync({}, 1)).toThrow()
    expect(() => parseSyncJSON('{"campaign_id":"1","campaign_id":"1"}')).toThrow()
    expect(() => parseSyncJSON('{"campaign_id":"1","campaign_\\u0069d":"1"}')).toThrow()
    expect(parseSyncJSON(JSON.stringify(syncFixture()))).toEqual(syncFixture())
    expect(parseSyncJSON(syncFixture())).toEqual(syncFixture())
  })
  it('subtracts request time and never uses wall clock for remaining duration', () => {
    const dto = syncFixture()
    expect(remainingMs(dto.valid_until!, dto.observed_at, 2000)).toBe(58000)
    expect(remainingMs(dto.valid_until!, dto.observed_at, 61000)).toBe(0)
    expect(remainingMs(dto.valid_until!, dto.observed_at, -1000)).toBe(60000)
    expect(remainingMs('2026-10-02T00:01:00.000000Z', '2026-10-02T00:00:00.000001Z', 0)).toBe(59999)
    expect(remainingMs('invalid', dto.observed_at, 0)).toBe(0)
  })
})
