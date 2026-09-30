import { beforeEach, describe, expect, it, vi } from 'vitest'
import { attemptKey, clearAttempt, idleState, readAttempt, storeAttempt, writesBlocked } from './campaignRecovery'
import type { Attempt } from './campaignRecovery'

function attempt(overrides: Partial<Attempt> = {}): Attempt {
  return { schema: 1, actorId: 1, attemptId: crypto.randomUUID(), kind: 'create', campaignId: null,
    baseVersion: null, sentAt: '2026-10-01T00:00:00Z', createName: 'Synthetic', ...overrides }
}
describe('Campaign same-tab recovery marker', () => {
  beforeEach(() => sessionStorage.clear())
  it('should retain minimal actor-owned unresolved metadata without a payload or credential', () => {
    const a = attempt()
    storeAttempt(sessionStorage, a)
    expect(readAttempt(sessionStorage, 1)).toEqual(a)
    expect(readAttempt(sessionStorage, 2)).toBeNull()
    expect(JSON.parse(sessionStorage.getItem(attemptKey(1)) ?? '{}')).toEqual(a)
    expect(() => storeAttempt(sessionStorage, attempt())).toThrow()
    expect(clearAttempt(sessionStorage, 2, a)).toBe(false)
    expect(clearAttempt(sessionStorage, 1, attempt())).toBe(false)
    expect(clearAttempt(sessionStorage, 1, a)).toBe(true)
    expect(readAttempt(sessionStorage, 1)).toBeNull()
  })
  it.each(['{', JSON.stringify({ ...attempt(), actorId: 2 }), JSON.stringify({ ...attempt(), token: 'PRIVATE' }), 'x'.repeat(2049), JSON.stringify({ ...attempt(), kind: 'patch' })])('should block corrupt input without trusting its address %s', value => {
    sessionStorage.setItem(attemptKey(1), value)
    expect(readAttempt(sessionStorage, 1)).toBe('corrupt')
    expect(clearAttempt(sessionStorage, 1, attempt())).toBe(false)
    expect(clearAttempt(sessionStorage, 1, 'corrupt')).toBe(true)
  })
  it('should reject oversize serialization and propagate storage access/quota failure', () => {
    expect(() => storeAttempt(sessionStorage, attempt({ createName: '😀'.repeat(200), attemptId: 'bad' }))).toThrow()
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('quota') })
    expect(() => storeAttempt(sessionStorage, attempt())).toThrow()
    spy.mockRestore()
    expect(clearAttempt(sessionStorage, 1, null)).toBe(false)
    expect(clearAttempt(sessionStorage, 1, 'corrupt')).toBe(false)
    expect(writesBlocked(idleState())).toBe(false)
    expect(writesBlocked({ ...idleState(), status: 'definiteError' })).toBe(false)
    expect(writesBlocked({ ...idleState(), status: 'unknown' })).toBe(true)
  })
})
