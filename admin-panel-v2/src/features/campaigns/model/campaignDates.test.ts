import { describe, expect, it } from 'vitest'
import { compareInstants, fromTashkentInput, parseInstant, toTashkentInput } from './campaignDates'

describe('Campaign aware dates', () => {
  it('should use explicit Tashkent seconds across UTC calendar days', () => {
    expect(fromTashkentInput('2026-10-01T23:30:45')).toBe('2026-10-01T23:30:45+05:00')
    expect(compareInstants('2026-10-01T23:30:45+05:00', '2026-10-01T18:30:45Z')).toBe(0)
    expect(toTashkentInput('2026-10-01T20:00:00Z')).toBe('2026-10-02T01:00:00')
    expect(fromTashkentInput('2026-10-01T00:00')).toBe('2026-10-01T00:00:00+05:00')
    expect(fromTashkentInput('')).toBeNull()
    expect(toTashkentInput(null)).toBe('')
  })
  it.each(['2026-02-30T12:00:00Z', '2026-01-01T00:00:00', '0000-01-01T00:00:00Z', '2026-13-01T00:00:00Z', '2026-01-00T00:00:00Z', '2026-01-01T24:00:00Z', '2026-01-01T00:60:00Z', '2026-01-01T00:00:60Z', '2026-01-01T00:00:00+24:00', '2026-01-01T00:00:00+05:60', 'bad'])('should reject invalid calendar or offset %s', value => {
    expect(parseInstant(value)).toBeNull()
  })
  it('should preserve microsecond ordering instead of rounding to milliseconds', () => {
    const a = '2026-10-01T00:00:00.123456+00:00', b = '2026-10-01T00:00:00.123457+00:00'
    expect(compareInstants(a, b)).toBe(-1)
    expect(compareInstants(b, a)).toBe(1)
    expect(compareInstants(a, '2026-10-01T05:00:00.1234560+05:00')).toBe(0)
    expect(compareInstants(a, '2026-10-02T00:00:00Z')).toBe(-1)
    expect(compareInstants('2026-10-02T00:00:00Z', a)).toBe(1)
    expect(() => compareInstants('bad', a)).toThrow()
    expect(() => toTashkentInput('bad')).toThrow()
    expect(() => fromTashkentInput('2026-02-30T12:00:00')).toThrow()
  })
})
