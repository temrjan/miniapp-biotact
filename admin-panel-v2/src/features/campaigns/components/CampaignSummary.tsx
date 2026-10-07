import { CAMPAIGN_ZONE, formatCampaignDate } from '../model/campaignDates'
import { STATE_LABELS } from '../types/campaign.types'
import type { CampaignRecord } from '../types/campaign.types'

export function CampaignSummary({ record }: { record: CampaignRecord }) {
  return <article className="space-y-2 wrap-break-word" aria-label={`Запись №${record.id}`}>
    <p>№{record.id} · {record.name} · {STATE_LABELS[record.state]}</p>
    <p>Товар: {record.product_id ?? 'Не задано'}</p>
    <p>{formatCampaignDate(record.start_at)} — {formatCampaignDate(record.end_at)} · {CAMPAIGN_ZONE}</p>
    <p>Версия: {record.updated_at}</p>
    <p className="whitespace-pre-wrap">Описание: {record.description ?? 'Не задано'}</p>
    <p className="whitespace-pre-wrap">Условия: {record.terms ?? 'Не задано'}</p>
  </article>
}
