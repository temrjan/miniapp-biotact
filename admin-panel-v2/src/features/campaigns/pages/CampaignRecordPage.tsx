import { Link, useParams } from 'react-router-dom'
import { Button } from '@/shared/components/ui/button'
import { useCampaign } from '../hooks/useCampaigns'
import { useCampaignActions } from '../hooks/useCampaignActions'
import { CampaignResultsPanel } from '../components/CampaignResultsPanel'
import { CampaignSyncPanel } from '../components/CampaignSyncPanel'
import { CampaignForm } from '../components/CampaignForm'
import { CampaignRecoveryPanel } from '../components/CampaignRecoveryPanel'

export function CampaignRecordPage() {
  const { id: pathId } = useParams()
  const parsed = Number(pathId)
  const id = pathId && /^[1-9]\d*$/.test(pathId) && Number.isSafeInteger(parsed) ? parsed : null
  const query = useCampaign(id)
  const { state } = useCampaignActions()
  return <div className="p-4 md:p-6 max-w-3xl space-y-5">
    <Link className="text-primary underline" to="/campaigns">К списку акций</Link>
    <h1 className="text-2xl font-bold">Акция</h1>
    {id === null ? <p role="alert">Некорректный ID акции.</p> : state.status === 'accessDenied' ? <CampaignRecoveryPanel /> : <>
      <Button variant="outline" disabled={query.isFetching} onClick={() => { void query.refetch() }}>Обновить запись</Button>
      {query.isFetching && <p role="status">Загрузка записи…</p>}
      {query.isError && <p role="alert">{query.error.message}</p>}
      {query.data && <CampaignSyncPanel key={`${state.session}:${id}:${query.data.updated_at}`} campaign={query.data} />}
      {query.data && <CampaignResultsPanel key={`results:${state.session}:${id}`} campaignId={id} />}
      {query.data ? <CampaignForm key={`${state.session}:${id}`} initial={query.data} available={!query.isError && !query.isFetching} /> : <CampaignRecoveryPanel />}
    </>}
  </div>
}
