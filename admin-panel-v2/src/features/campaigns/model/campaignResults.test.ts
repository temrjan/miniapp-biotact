import { describe, expect, it } from 'vitest'
import { decodeResults } from './campaignResults'
import { resultFixture, resultTotals } from '../test/resultsFixture'

describe('Results runtime contract', () => {
  it('accepts exact quantities beyond Number precision, zero, preparing and failed rows', () => {
    const big = resultFixture()
    big.items = [{ employee_code: '11111111-1111-4111-8111-111111111111', employee_display_name: 'Astral 😀', state: 'observed', totals: resultTotals('9007199254740993', '1', '9007199254740992') }]
    big.totals = resultTotals('9007199254740993', '1', '9007199254740992')
    expect(decodeResults(big, 1).totals?.net_quantity).toBe('9007199254740992')
    expect(decodeResults({ ...resultFixture(), items: [], totals: resultTotals('0','0','0') }, 1).state).toBe('observed')
    expect(decodeResults({ ...resultFixture(), state: 'preparing', items: [], totals: null }, 1).state).toBe('preparing')
    const failed = resultFixture(); failed.state = 'incomplete'; failed.totals = null
    const first = failed.items[0]; if (!first) throw new Error('Fixture missing')
    first.state = 'incomplete'; first.totals = null
    expect(decodeResults(failed, 1).items[1]?.totals).toEqual(resultTotals('2','0','2'))
  })
  it.each([
    { schema_version: true }, { campaign_id: '2' }, { campaign_id: '01' }, { coverage_scope: 'all' }, { secret: 'sentinel' },
    { observed_at: '2026-02-30T00:00:00.000000Z' }, { observed_at: '2026-10-05T12:00:00Z' },
    { state: 'incomplete', totals: null }, { state: 'preparing' }, { totals: null },
    { totals: resultTotals('6','1','5') },
  ])('rejects invalid report %j', changes => expect(() => decodeResults({ ...resultFixture(), ...changes }, 1)).toThrow())
  it.each([
    { employee_code: '11111111-1111-1111-8111-111111111111' }, { employee_display_name: ' ' }, { employee_display_name: '\uD800' },
    { employee_display_name: 'a'.repeat(162) }, { totals: null }, { state: 'unavailable' }, { totals: resultTotals('3','4','0') },
    { totals: resultTotals('03','1','2') }, { totals: resultTotals('3\n','1','2') }, { totals: resultTotals('3','1','3') },
  ])('rejects invalid row %j', changes => {
    const value = resultFixture(), first = value.items[0]
    expect(() => decodeResults({ ...value, items: [{ ...first, ...changes }, ...value.items.slice(1)] }, 1)).toThrow()
  })
  it('rejects duplicate and unsorted codes and missing keys', () => {
    const value = resultFixture()
    expect(() => decodeResults({ ...value, items: [...value.items, value.items[0]] }, 1)).toThrow()
    expect(() => decodeResults({ ...value, items: [...value.items].reverse() }, 1)).toThrow()
    const { totals: removed, ...partial } = value; expect(removed).not.toBeNull()
    expect(() => decodeResults(partial, 1)).toThrow()
  })
})
