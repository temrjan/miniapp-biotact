import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button } from '@/shared/components/ui/button'
import { campaignsApi } from '../api/campaignsApi'
import { useCampaignActions } from '../hooks/useCampaignActions'
import { CAMPAIGN_ZONE, formatCampaignDate } from '../model/campaignDates'

const failureText = { incomplete: 'Данные неполные', inconsistent: 'Данные требуют проверки', unavailable: 'Данные недоступны' }
const PAGE_SIZE = 20
export function CampaignResultsPanel({ campaignId }: { campaignId: number }) {
  const [page, setPage] = useState(1)
  const { controller, actorId, state } = useCampaignActions()
  const query = useQuery({
    queryKey: ['campaigns', actorId, 'results', state.session, campaignId],
    queryFn: ({ signal }) => controller.read(() => campaignsApi.results(campaignId, signal)),
    enabled: actorId !== null && state.status !== 'accessDenied',
    retry: false, networkMode: 'always', refetchOnWindowFocus: false, refetchOnReconnect: false,
    staleTime: 0, gcTime: 0,
  })
  if (state.status === 'accessDenied' || actorId === null) return null
  const report = !query.isFetching && !query.isError ? query.data : undefined
  const pages = Math.max(1, Math.ceil((report?.items.length ?? 0) / PAGE_SIZE))
  const currentPage = Math.min(page, pages)
  const offset = (currentPage - 1) * PAGE_SIZE
  return <section aria-label="Результаты" className="rounded-lg border p-4 space-y-3 wrap-break-word">
    <h2 className="font-semibold">Результаты</h2>
    {query.isFetching ? <p role="status">Загрузка результатов…</p> : query.isError ?
      <p role="alert">Не удалось получить результаты. Повторите позже.</p> : report && <>
        <p className="text-sm text-muted-foreground">Данные получены порталом. Новые оплаты и возвраты могут появиться после доставки событий.</p>
        <p className="text-sm">Прочитано: {formatCampaignDate(report.observed_at)} · {CAMPAIGN_ZONE}</p>
        {report.state === 'preparing' ? <p role="status">Результаты ещё не подготовлены.</p> : <>
          <p className="font-medium">Участников акции: {report.items.length}</p>
          {report.items.length === 0 && <p role="status">Участников этой акции пока нет.</p>}
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr>{['№', 'Сотрудник', 'Оплачено, шт.', 'Возвращено, шт.', 'Итого, шт.'].map(text => <th key={text} scope="col" className="p-2 text-left">{text}</th>)}</tr></thead>
              <tbody>{report.items.slice(offset, offset + PAGE_SIZE).map((row, index) => <tr key={row.employee_code}>
                <td className="p-2">{offset + index + 1}</td>
                <th scope="row" className="p-2 text-left font-normal wrap-break-word">{row.employee_display_name}{row.state !== 'observed' && <p role="alert">{failureText[row.state]}</p>}</th>
                <td className="p-2">{row.totals?.paid_quantity ?? '—'}</td><td className="p-2">{row.totals?.returned_quantity ?? '—'}</td><td className="p-2">{row.totals?.net_quantity ?? '—'}</td>
              </tr>)}</tbody>
              {report.totals && <tfoot><tr><th scope="row" colSpan={2} className="p-2 text-left">Всего по акции</th><td className="p-2">{report.totals.paid_quantity}</td><td className="p-2">{report.totals.returned_quantity}</td><td className="p-2">{report.totals.net_quantity}</td></tr></tfoot>}
            </table>
          </div>
          {pages > 1 && <nav aria-label="Страницы участников акции" className="flex flex-wrap items-center gap-3">
            <Button type="button" variant="outline" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>Предыдущая страница</Button>
            <p>Строки {offset + 1}–{Math.min(offset + PAGE_SIZE, report.items.length)} из {report.items.length} · Страница {currentPage} из {pages}</p>
            <Button type="button" variant="outline" disabled={currentPage >= pages} onClick={() => setPage(currentPage + 1)}>Следующая страница</Button>
          </nav>}
          {report.state !== 'observed' && <p role="alert">Общий итог недоступен: не все строки проверены.</p>}
        </>}
      </>}
    <Button type="button" variant="outline" className="whitespace-normal h-auto min-h-10" disabled={query.isFetching}
      onClick={() => { setPage(1); void query.refetch() }}>Обновить результаты</Button>
  </section>
}
