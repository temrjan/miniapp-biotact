import type { CampaignSync } from '../model/campaignSync'
export function syncFixture(changes: Partial<CampaignSync> = {}): CampaignSync {
  return {
    campaign_id: '1', campaign_state: 'published', sender_enabled: true, status: 'confirmed',
    desired_revision: '9223372036854775807', confirmed_revision: '9223372036854775807', participant_count: 2,
    confirmed_at: '2026-10-02T00:00:00.000000Z', valid_until: '2026-10-02T00:01:00.000000Z',
    stop_confirmed: false, stop_deadline: null, pending_count: 0, blocked_count: 0, oldest_pending_at: null,
    last_error_code: null, observed_at: '2026-10-02T00:00:00.000000Z', ...changes,
  }
}
