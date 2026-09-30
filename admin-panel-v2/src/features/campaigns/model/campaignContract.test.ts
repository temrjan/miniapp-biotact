import { describe, expect, it } from 'vitest'
import { decodeError, decodePage, decodeRecord, ERROR_STATUS } from './campaignContract'
import { campaign, campaignPage } from '../test/fixtures'

describe('Campaign contract boundary', () => {
  it('should preserve every raw version and nullable key in a valid response', () => {
    expect(decodeRecord(campaign(), 1, 'draft')).toEqual(campaign())
    expect(decodePage(campaignPage([campaign()]), 1).items[0]?.updated_at).toBe(campaign().updated_at)
    expect(decodeRecord(campaign({ updated_at: '2026-10-01T00:00:00.123457+00:00' }), 1, 'draft', campaign().updated_at).updated_at).toContain('.123457')
    expect(decodePage(campaignPage(), 1).pages).toBe(0)
  })
  it.each([{ id: '1' }, { id: true }, { id: Number.MAX_SAFE_INTEGER + 1 }, { state: 'active' }, { start_at: '2026-02-30T00:00:00Z' }, { debug: 'PRIVATE' }, { product_id: -1 }, { updated_at: null }])('should reject malformed record %j', value => {
    expect(() => decodeRecord({ ...campaign(), ...value })).toThrow()
  })
  it('should reject a missing nullable key and wrong addressed identity, state or unchanged version', () => {
    const { terms, ...missing } = campaign()
    expect(terms).toBeNull()
    expect(() => decodeRecord(missing)).toThrow()
    expect(() => decodeRecord(campaign(), 2)).toThrow()
    expect(() => decodeRecord(campaign(), 1, 'published')).toThrow()
    expect(() => decodeRecord(campaign(), 1, 'draft', campaign().updated_at)).toThrow()
  })
  it.each([{ page: 2 }, { size: 10 }, { total: 1, pages: 0 }, { total: -1 }, { extra: 1 }])('should reject inconsistent page %j', value => {
    expect(() => decodePage({ ...campaignPage(), ...value }, 1)).toThrow()
  })
  it.each(Object.entries(ERROR_STATUS))('should recognize only the exact status/code pair %s', (code, status) => {
    const body = { detail: { code, message: '<PRIVATE>' } }
    expect(decodeError(status, body)).toBe(code)
    expect(decodeError(400, body)).toBeNull()
    expect(decodeError(status, { detail: { ...body.detail, debug: 'PRIVATE' } })).toBeNull()
    expect(decodeError(status, { ...body, extra: true })).toBeNull()
  })
  it('should reject arbitrary HTML, diagnostics and unknown codes', () => {
    expect(decodeError(503, '<html>PRIVATE</html>')).toBeNull()
    expect(decodeError(503, { detail: { code: 'unknown', message: 'PRIVATE' } })).toBeNull()
  })
})
