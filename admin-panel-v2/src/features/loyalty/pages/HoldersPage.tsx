import { useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Button } from '@/shared/components/ui/button'
import { useTicketHolders } from '../hooks/useLoyalty'
import type { HoldersFilter, TicketHolder } from '../types'

const FILTERS: { value: HoldersFilter; label: string; hint: string }[] = [
  { value: 'all', label: 'Все держатели', hint: 'у кого есть хотя бы один билет' },
  { value: 'clean', label: 'Билеты на руках', hint: 'ещё не участвовали в тираже' },
  {
    value: 'owed',
    label: 'Ждут билет',
    hint: 'анкета есть, билета за неё нет — свежие сверху',
  },
]

const SOURCE_LABELS: Record<keyof TicketHolder['by_source'], string> = {
  survey: 'анкета',
  friend: 'Друг',
  activation: 'покупка',
}

const REASON_LABELS: Record<'survey' | 'age', string> = {
  survey: 'не заполнена анкета',
  age: 'не подтверждено 18+',
}

const isFilter = (value: string | null): value is HoldersFilter =>
  value === 'all' || value === 'clean' || value === 'owed'

/** «за что» in one cell: sources the member actually holds, in issue order. */
const describeSources = (holder: TicketHolder): string => {
  const parts = (['survey', 'friend', 'activation'] as const)
    .filter((source) => holder.by_source[source] > 0)
    .map((source) =>
      holder.by_source[source] > 1
        ? `${SOURCE_LABELS[source]} ×${holder.by_source[source]}`
        : SOURCE_LABELS[source]
    )
  return parts.length > 0 ? parts.join(' · ') : '—'
}

/** Where the tickets are now — one draw, never the whole history. */
const describePlace = (holder: TicketHolder): string => {
  if (holder.draw_number !== null) {
    return `Розыгрыш №${holder.draw_number}: ${holder.numbers.join(', ')}`
  }
  if (holder.played > 0) {
    return holder.won ? 'Сыграли — выигрыш' : 'Сыграли'
  }
  return holder.tickets_clean > 0 ? 'На руках' : '—'
}

const formatDate = (iso: string | null): string =>
  iso === null ? 'ни разу' : new Date(iso).toLocaleDateString('ru-RU')

export const HoldersPage = () => {
  const [params, setParams] = useSearchParams()
  const raw = params.get('filter')
  const filter: HoldersFilter = isFilter(raw) ? raw : 'all'
  const [page, setPage] = useState(1)
  const { data, isLoading, error } = useTicketHolders(filter, page)

  const selectFilter = (next: HoldersFilter) => {
    setPage(1)
    setParams({ filter: next })
  }

  const pages = data ? Math.max(1, Math.ceil(data.total / data.size)) : 1

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold">Держатели билетов</h1>
          <p className="text-sm text-muted-foreground">
            Кто получил билеты и на каком основании
          </p>
        </div>
        <Button variant="outline" asChild>
          <Link to="/loyalty">← К дашборду</Link>
        </Button>
      </div>

      <div className="flex gap-2 flex-wrap">
        {FILTERS.map((item) => (
          <Button
            key={item.value}
            variant={item.value === filter ? 'default' : 'outline'}
            size="sm"
            onClick={() => selectFilter(item.value)}
            title={item.hint}
          >
            {item.label}
          </Button>
        ))}
      </div>

      {filter === 'owed' && (
        <p className="text-sm text-muted-foreground border rounded-md p-3">
          Отсортировано по последней активации, свежие сверху. Свежая дата в верхних строках
          означает, что выдача билетов сломалась: активация выдаёт билеты всех видов. Только старые
          даты и «ни разу» — норма: билеты задним числом не начислялись.
        </p>
      )}

      {isLoading && <p className="text-sm text-muted-foreground">Загрузка...</p>}

      {error && (
        <p className="text-sm text-destructive">
          Ошибка загрузки данных: {error instanceof Error ? error.message : 'Unknown error'}
        </p>
      )}

      {data && data.items.length === 0 && (
        <p className="text-sm text-muted-foreground">Никого нет — по этому фильтру список пуст.</p>
      )}

      {data && data.items.length > 0 && (
        <div className="border rounded-md overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-muted/50">
              <tr>
                <th className="text-left p-3 font-medium">Участник</th>
                <th className="text-left p-3 font-medium">Билетов</th>
                <th className="text-left p-3 font-medium">За что</th>
                <th className="text-left p-3 font-medium">Где сейчас</th>
                <th className="text-left p-3 font-medium">
                  {filter === 'owed' ? 'Последняя активация' : 'В тираж'}
                </th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((holder) => (
                <tr key={holder.user_id} className="border-t">
                  <td className="p-3">
                    <div className="font-medium">{holder.name}</div>
                    <div className="text-xs text-muted-foreground">{holder.phone ?? '—'}</div>
                  </td>
                  <td className="p-3">
                    {holder.tickets_total}
                    {holder.tickets_clean !== holder.tickets_total && (
                      <span className="text-xs text-muted-foreground">
                        {' '}
                        (на руках {holder.tickets_clean})
                      </span>
                    )}
                  </td>
                  <td className="p-3">{describeSources(holder)}</td>
                  <td className="p-3">{describePlace(holder)}</td>
                  <td className="p-3">
                    {filter === 'owed'
                      ? formatDate(holder.last_activation_at)
                      : holder.eligible_now
                        ? 'да'
                        : `нет — ${REASON_LABELS[holder.not_eligible_reason ?? 'survey']}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data && data.total > data.size && (
        <div className="flex items-center gap-3">
          <Button
            variant="outline"
            size="sm"
            disabled={page <= 1}
            onClick={() => setPage((current) => current - 1)}
          >
            Назад
          </Button>
          <span className="text-sm text-muted-foreground">
            Страница {data.page} из {pages} · всего {data.total}
          </span>
          <Button
            variant="outline"
            size="sm"
            disabled={page >= pages}
            onClick={() => setPage((current) => current + 1)}
          >
            Вперёд
          </Button>
        </div>
      )}
    </div>
  )
}
