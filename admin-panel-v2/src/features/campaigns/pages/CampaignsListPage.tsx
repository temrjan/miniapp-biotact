import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Button } from '@/shared/components/ui/button'
import { useCampaigns } from '../hooks/useCampaigns'
import { useCampaignActions } from '../hooks/useCampaignActions'
import { CampaignRecoveryPanel } from '../components/CampaignRecoveryPanel'
import { CampaignSummary } from '../components/CampaignSummary'
import type { CampaignState } from '../types/campaign.types'

export function CampaignsListPage() {
  const { state } = useCampaignActions()
  return <CampaignList key={state.session} />
}

function CampaignList() {
  const [filter, setFilter] = useState<CampaignState | undefined>(), [page, setPage] = useState(1)
  const query = useCampaigns(page, filter)
  const { state, blocked } = useCampaignActions()
  if (!query.isFetching && !query.isError && query.data && query.data.items.length === 0 && page > Math.max(1, query.data.pages)) {
    setPage(Math.max(1, query.data.pages))
  }
  const available = !query.isError && !!query.data && state.status !== 'accessDenied'
  return <div className="p-4 md:p-6 space-y-5 max-w-5xl">
    <h1 className="text-2xl font-bold">Акции</h1>
    <CampaignRecoveryPanel />
    <div className="flex flex-wrap gap-3 items-center">
      <label htmlFor="campaign-filter">Состояние</label>
      <select id="campaign-filter" className="border rounded-md p-2 bg-background" value={filter ?? 'all'} onChange={e => {
        const value = e.target.value
        setFilter(value === 'draft' || value === 'published' || value === 'ended' ? value : undefined); setPage(1)
      }}>
        <option value="all">Все</option><option value="draft">Черновик</option>
        <option value="published">Опубликована</option><option value="ended">Завершена</option>
      </select>
      <Button variant="outline" onClick={() => { void query.refetch() }} disabled={query.isFetching || state.status === 'accessDenied'}>Обновить список</Button>
      {available && !blocked ? <Link to="/campaigns/new" className="text-primary underline">Создать черновик</Link> : <Button disabled>Создать черновик</Button>}
    </div>
    {state.status !== 'accessDenied' && <>
      {query.isFetching ? <p role="status">{page > 1 ? 'Загрузка страницы…' : 'Загрузка акций…'}</p> : query.isError ?
        <p role="alert">{query.error.message}</p> : query.data && <>
          {query.data.total === 0 ? <p>Акций пока нет.</p> : <div className="space-y-3">{query.data.items.map(record => <div className="p-4 border rounded-lg space-y-3" key={record.id}>
            <CampaignSummary record={record} /><Link className="text-primary underline" to={`/campaigns/${record.id}`}>Открыть акцию №{record.id}</Link>
          </div>)}</div>}
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="outline" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Предыдущая страница</Button>
            <p>Страница {page} из {Math.max(1, query.data.pages)} · Всего {query.data.total}</p>
            <Button variant="outline" disabled={page >= query.data.pages} onClick={() => setPage(p => p + 1)}>Следующая страница</Button>
          </div>
        </>}
    </>}
  </div>
}
