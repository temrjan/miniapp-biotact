import { Link } from 'react-router-dom'
import { useCurrentDraw } from '../hooks/useLoyalty'
import { DrawFreezeCard } from '../components/DrawFreezeCard'
import { DrawLiveCard } from '../components/DrawLiveCard'
import { DrawResultCard } from '../components/DrawResultCard'
import { DrawCancelledCard } from '../components/DrawCancelledCard'

/** The draw conducting page: one component, four states by the latest draw's status.
 * Loading and read-error states follow the dashboard pattern (Гейт-1, уточнение 4) —
 * this page is the administrator's workplace during the draw itself. */
export const DrawPage = () => {
  const { data, isLoading, error } = useCurrentDraw()

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <div className="inline-block h-8 w-8 animate-spin rounded-full border-4 border-solid border-current border-r-transparent" />
          <p className="mt-4 text-sm text-muted-foreground">Загрузка...</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-sm text-destructive">
            Ошибка загрузки данных: {error instanceof Error ? error.message : 'Unknown error'}
          </p>
        </div>
      </div>
    )
  }

  if (!data) {
    return null
  }

  const { draw } = data

  return (
    <div className="p-8 space-y-8 max-w-4xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Проведение розыгрыша</h1>
          <p className="text-muted-foreground mt-1">
            <Link to="/loyalty" className="underline">
              ← Назад к дашборду
            </Link>
          </p>
        </div>
      </div>

      {draw === null && (
        <DrawFreezeCard
          eligibleTickets={data.eligible_tickets}
          cleanTickets={data.clean_tickets}
        />
      )}
      {draw?.status === 'committed' && <DrawLiveCard draw={draw} />}
      {draw?.status === 'drawn' && (
        <>
          <DrawResultCard draw={draw} />
          <DrawFreezeCard
            eligibleTickets={data.eligible_tickets}
            cleanTickets={data.clean_tickets}
            triggerLabel="Заморозить следующий"
          />
        </>
      )}
      {draw?.status === 'cancelled' && (
        <>
          <DrawCancelledCard draw={draw} />
          <DrawFreezeCard
            eligibleTickets={data.eligible_tickets}
            cleanTickets={data.clean_tickets}
            triggerLabel="Заморозить"
          />
        </>
      )}

      {/* The winners of the previous draw stay reachable after the next freeze (БЛОКЕР-1). */}
      {data.last_drawn && <DrawResultCard draw={data.last_drawn} />}
    </div>
  )
}
