import type { CampaignResults } from '../model/campaignResults'
export const resultTotals = (paid = '5', returned = '1', net = '4') => ({ paid_quantity: paid, returned_quantity: returned, net_quantity: net })
export function resultFixture(): CampaignResults {
  return { schema_version: 1, campaign_id: '1', campaign_state: 'published', state: 'observed', coverage_scope: 'retained-source-v1', observed_at: '2026-10-05T12:00:00.123456Z',
    items: [
      { employee_code: '11111111-1111-4111-8111-111111111111', employee_display_name: 'Employee A', state: 'observed', totals: resultTotals('3', '1', '2') },
      { employee_code: '22222222-2222-4222-8222-222222222222', employee_display_name: 'Employee B', state: 'observed', totals: resultTotals('2', '0', '2') },
      { employee_code: '33333333-3333-4333-8333-333333333333', employee_display_name: 'Employee C', state: 'observed', totals: resultTotals('0', '0', '0') },
    ], totals: resultTotals() }
}
