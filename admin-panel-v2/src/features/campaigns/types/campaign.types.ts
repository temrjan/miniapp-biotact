export type CampaignState = 'draft' | 'published' | 'ended'

export interface CampaignFields {
  name: string
  product_id: number | null
  image_url: string | null
  description: string | null
  terms: string | null
  start_at: string | null
  end_at: string | null
}

export interface CampaignRecord extends CampaignFields {
  id: number
  state: CampaignState
  published_at: string | null
  ended_at: string | null
  created_at: string
  updated_at: string
}

export interface CampaignPage {
  items: CampaignRecord[]
  total: number
  page: number
  size: number
  pages: number
}

export interface DraftInput extends Partial<Omit<CampaignFields, 'name'>> { name: string }
export interface EditInput extends Partial<CampaignFields> { expected_updated_at: string }
export type WriteKind = 'create' | 'patch' | 'publish' | 'end'

export type CampaignErrorCode =
  | 'invalid_admin_auth' | 'admin_forbidden' | 'campaign_not_found'
  | 'campaign_changed' | 'invalid_campaign_state' | 'immutable_campaign' | 'campaign_overlap'
  | 'invalid_request' | 'invalid_campaign_dates' | 'incomplete_campaign' | 'invalid_image_url'
  | 'product_not_found' | 'product_inactive' | 'product_not_purchasable' | 'product_out_of_stock'
  | 'shop_unavailable' | 'service_unavailable' | 'campaign_service_unavailable' | 'campaign_result_unknown'

export const STATE_LABELS: Record<CampaignState, string> = {
  draft: 'Черновик', published: 'Опубликована', ended: 'Завершена',
}
