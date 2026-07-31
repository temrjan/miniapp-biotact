import { useParams } from 'react-router-dom'
import { Button } from '@/shared/components/ui/button'
import { usePrintList } from '../hooks/useLoyalty'

/** The printed list — bare page, no navigation, laid out for the browser's own print.
 * No PDF is generated anywhere (ratified): printing a web page is what browsers do.
 * The total count N is repeated as text, not only implied by the table: without it
 * the range for the external draw cannot be set, and that is the point of the list. */
export const DrawPrintPage = () => {
  const { drawId } = useParams<{ drawId: string }>()
  const id = drawId === undefined ? undefined : Number(drawId)
  const { data, isLoading, error } = usePrintList(id)

  if (isLoading) {
    return <p className="p-8 text-sm text-muted-foreground">Загрузка...</p>
  }
  if (error || !data) {
    return (
      <p className="p-8 text-sm text-destructive">
        Ошибка загрузки: {error instanceof Error ? error.message : 'Unknown error'}
      </p>
    )
  }

  return (
    <div className="p-8 max-w-3xl mx-auto print:p-4">
      <style>{`@media print { .no-print { display: none; } }`}</style>

      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Розыгрыш №{data.draw_number} — список билетов</h1>
          <p className="text-muted-foreground">
            {data.conducted_on ? `Дата проведения: ${data.conducted_on}` : 'Дата уточняется'}
          </p>
        </div>
        <Button className="no-print" onClick={() => window.print()}>
          Печать
        </Button>
      </div>

      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b">
            <th className="text-left py-1 pr-8">Билет №</th>
            <th className="text-left py-1">Последние 4 цифры телеграм-ID</th>
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row) => (
            <tr key={row.ticket_number} className="border-b">
              <td className="py-1 pr-8">{row.ticket_number}</td>
              <td className="py-1">{row.telegram_last4}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="mt-6 font-semibold">Всего билетов: {data.total_tickets}</p>
    </div>
  )
}
