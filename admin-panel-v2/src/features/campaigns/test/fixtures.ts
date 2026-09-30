import type { CampaignRecord } from '../types/campaign.types'

export function campaign(overrides: Partial<CampaignRecord> = {}): CampaignRecord {
  return { id: 1, name: 'Synthetic campaign', product_id: null, image_url: null, description: null, terms: null,
    state: 'draft', start_at: null, end_at: null, published_at: null, ended_at: null,
    created_at: '2026-10-01T00:00:00.123456+00:00', updated_at: '2026-10-01T00:00:00.123456+00:00', ...overrides }
}
export function campaignPage(items: CampaignRecord[] = [], page = 1, total = items.length) {
  return { items, page, size: 20, total, pages: Math.ceil(total / 20) }
}
