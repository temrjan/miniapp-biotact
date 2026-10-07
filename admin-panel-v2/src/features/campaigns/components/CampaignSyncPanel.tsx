import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/shared/components/ui/button'
import { campaignsApi } from '../api/campaignsApi'
import { useCampaignActions } from '../hooks/useCampaignActions'
import { EXPIRED_TEXT, remainingMs } from '../model/campaignSync'
import { CAMPAIGN_ZONE, formatCampaignDate } from '../model/campaignDates'
import type { CampaignRecord } from '../types/campaign.types'
import type { CampaignSync } from '../model/campaignSync'

interface Observation { data: CampaignSync; started: number; received: number }
function text({ data, started }: Observation, now: number): string {
  switch (data.status) {
    case 'not_published': return 'Доставка начнётся после публикации акции.'
    case 'disabled': return 'Отправка в магазин выключена.'
    case 'blocked': return 'Доставка требует проверки ответственным за систему.'
    case 'expired': return EXPIRED_TEXT
    case 'confirmed':
      if (data.campaign_state === 'ended') return data.stop_confirmed ? 'Завершение в магазине подтверждено.' : 'Ожидается подтверждение завершения в магазине.'
      if (data.campaign_state !== 'published' || data.valid_until === null || remainingMs(data.valid_until, data.observed_at, now - started) === 0) return EXPIRED_TEXT
      return `Доставка подтверждена. Срок подтверждения: ${formatCampaignDate(data.valid_until)}.`
    case 'pending':
      if (data.campaign_state !== 'ended') return 'Ожидается подтверждение доставки в магазин.'
      return data.stop_deadline !== null && remainingMs(data.stop_deadline, data.observed_at, now - started) === 0 ?
        'Предыдущая копия истекла. Подтверждение завершения ещё ожидается.' : 'Ожидается подтверждение завершения в магазине.'
  }
}
export function CampaignSyncPanel({ campaign }: { campaign: CampaignRecord }) {
  const { controller, actorId, state } = useCampaignActions()
  const [now, setNow] = useState(() => performance.now())
  const query = useQuery({
    queryKey: ['campaigns', actorId, 'sync', state.session, campaign.id, campaign.state, campaign.updated_at],
    queryFn: ({ signal }) => controller.read(async () => {
      const started = performance.now()
      const data = await campaignsApi.sync(campaign.id, signal)
      return { data, started, received: performance.now() }
    }),
    enabled: actorId !== null && state.status !== 'accessDenied',
    retry: false, networkMode: 'always', refetchOnWindowFocus: 'always', refetchOnReconnect: false,
    staleTime: 0, gcTime: 0,
  })
  const observation = query.data
  useEffect(() => {
    if (!observation) return
    const data = observation.data
    const deadline = data.status === 'confirmed' && data.campaign_state === 'published' ? data.valid_until :
      data.status === 'pending' && data.campaign_state === 'ended' ? data.stop_deadline : null
    if (deadline === null) return
    const timer = window.setTimeout(() => setNow(performance.now()),
      remainingMs(deadline, data.observed_at, performance.now() - observation.started))
    return () => window.clearTimeout(timer)
  }, [observation])
  const valid = observation?.data.campaign_state === campaign.state
  return <section aria-label="Доставка в магазин" className="rounded-lg border p-4 space-y-3 wrap-break-word">
    <h2 className="font-semibold">Доставка в магазин</h2>
    {query.isError || observation && !valid ? <p role="alert">Не удалось проверить доставку. Повторите позже.</p> :
      !observation ? <p role="status">Проверяется доставка в магазин…</p> : <>
        <p role="status">{text(observation, Math.max(now, observation.received))}</p>
        <p className="text-sm text-muted-foreground">Участников: {observation.data.participant_count} · {CAMPAIGN_ZONE}</p>
        {observation.data.confirmed_at && <p className="text-sm">Подтверждено: {formatCampaignDate(observation.data.confirmed_at)}</p>}
      </>}
    <Button type="button" variant="outline" className="whitespace-normal h-auto min-h-10" disabled={query.isFetching}
      onClick={() => { void query.refetch() }}>Обновить синхронизацию</Button>
  </section>
}
