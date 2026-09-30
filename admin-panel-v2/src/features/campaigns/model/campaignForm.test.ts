import { describe, expect, it } from 'vitest'
import { EMPTY_VALUES, formInput, isDirty, recordValues, validImageUrl } from './campaignForm'
import { campaign } from '../test/fixtures'

describe('Campaign editable intent', () => {
  it('should allow a name-only incomplete draft and Unicode code-point limits', () => {
    expect(formInput({ ...EMPTY_VALUES, name: 'x' })).toMatchObject({ name: 'x', product_id: null, terms: null })
    expect(formInput({ ...EMPTY_VALUES, name: '😀'.repeat(200) })?.name).toBe('😀'.repeat(200))
    expect(() => formInput({ ...EMPTY_VALUES, name: '😀'.repeat(201) })).toThrow()
    expect(() => formInput(EMPTY_VALUES)).toThrow()
  })
  it('should send only actual changes, intentional clearing and the exact saved version', () => {
    const record = campaign({ image_url: 'https://shop.test/uploads/a.webp', start_at: '2026-10-01T00:00:00.123456+00:00', end_at: '2026-10-01T00:00:00.123457+00:00' })
    const values = recordValues(record)
    expect(isDirty(values, record)).toBe(false)
    expect(formInput(values, record)).toBeNull()
    expect(formInput({ ...values, image_url: '' }, record)).toEqual({ expected_updated_at: record.updated_at, image_url: null })
    expect(formInput({ ...values, name: 'edited' }, record)).toEqual({ expected_updated_at: record.updated_at, name: 'edited' })
    expect(isDirty({ ...values, name: 'edited' }, record)).toBe(true)
    expect(formInput({ ...values, name: 'edited', start_at: '2026-10-01T04:00:00' }, record)).toEqual({
      expected_updated_at: record.updated_at, name: 'edited', start_at: '2026-10-01T04:00:00+05:00',
    })
    expect(formInput({ ...values, name: record.name }, record)).toBeNull()
  })
  it.each(['0', '-1', '1.1', '9007199254740992', '1e2', 'abc'])('should reject invalid product ID %s', product_id => {
    expect(() => formInput({ ...EMPTY_VALUES, name: 'x', product_id })).toThrow()
  })
  it('should validate merged dates without rounding microseconds', () => {
    const record = campaign({ start_at: '2026-10-01T00:00:00.123457Z', end_at: '2026-10-01T00:00:00.123456Z' })
    expect(() => formInput(recordValues(record), record)).toThrow('раньше')
    expect(() => formInput({ ...EMPTY_VALUES, name: 'x', start_at: '2026-10-01T00:00:00', end_at: '2026-10-01T00:00:00' })).toThrow()
    expect(formInput({ ...EMPTY_VALUES, name: 'x', product_id: '21', start_at: '2026-10-01T00:00:00' })).toMatchObject({ product_id: 21, start_at: '2026-10-01T00:00:00+05:00' })
  })
  it.each(['http://shop.test/uploads/a.webp', 'https://user:pw@shop.test/uploads/a.webp', 'https://shop.test/uploads/a.webp?q=1', 'https://shop.test/uploads/a.webp#f', 'https://shop.test/uploads/../a.webp', 'https://shop.test/uploads/%2e%2e/a.webp', 'https://shop.test/uploads/%252e%252e/a.webp', 'https://shop.test/uploads/', 'bad', 'https://shop.test/other/a.webp'])('should reject unsafe image %s', image => {
    expect(validImageUrl(image)).toBe(false)
    expect(() => formInput({ ...EMPTY_VALUES, name: 'x', image_url: image })).toThrow()
  })
  it('should accept a shop upload path while deferring exact origin to the portal', () => {
    expect(validImageUrl('https://shop.test/uploads/products/a.webp')).toBe(true)
    expect(() => formInput({ ...EMPTY_VALUES, name: 'x', image_url: `https://shop.test/uploads/${'a'.repeat(500)}` })).toThrow()
  })
})
