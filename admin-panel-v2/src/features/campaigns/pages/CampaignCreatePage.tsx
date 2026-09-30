import { Link, useLocation } from 'react-router-dom'
import { useCampaigns } from '../hooks/useCampaigns'
import { useCampaignActions } from '../hooks/useCampaignActions'
import { CampaignForm } from '../components/CampaignForm'
import { CampaignRecoveryPanel } from '../components/CampaignRecoveryPanel'

export function CampaignCreatePage() {
  const query = useCampaigns(1)
  const { state } = useCampaignActions()
  const location = useLocation()
  return <div className="p-4 md:p-6 max-w-3xl space-y-5">
    <Link className="text-primary underline" to="/campaigns">К списку акций</Link>
    <h1 className="text-2xl font-bold">Новый черновик</h1>
    {state.status === 'accessDenied' ? <CampaignRecoveryPanel /> : <>
      {query.isFetching && <p role="status">Проверка подключения акций…</p>}
      {query.isError && <p role="alert">{query.error.message}</p>}
      <CampaignForm key={`${state.session}:${location.key}`} available={!!query.data && !query.isError && !query.isFetching} />
    </>}
  </div>
}
