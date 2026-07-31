import { Link } from 'react-router-dom'
import { Printer } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/components/ui/table'
import { CopyTextButton } from './CopyTextButton'
import { fingerprintPostText } from './drawTexts'
import type { CurrentDraw } from '../types'

/** The finished draw: who won what, plus the fingerprint and the printed list for the archive. */
export const DrawResultCard = ({ draw }: { draw: CurrentDraw }) => {
  return (
    <section className="border rounded-lg p-6 space-y-4">
      <div>
        <h2 className="text-2xl font-bold">Розыгрыш №{draw.draw_number} разыгран</h2>
        {draw.conducted_on && (
          <p className="text-muted-foreground">Проведён: {draw.conducted_on}</p>
        )}
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Приз</TableHead>
            <TableHead>Билет</TableHead>
            <TableHead>Победитель</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {draw.winners.map((winner) => (
            <TableRow key={winner.ticket_number}>
              <TableCell>
                {winner.prize_rank === 1 ? 'Главный' : `${winner.prize_rank}-й`}
              </TableCell>
              <TableCell>№{winner.ticket_number}</TableCell>
              <TableCell>{winner.first_name ?? '—'}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>

      <div className="flex gap-3">
        <CopyTextButton text={fingerprintPostText(draw)} label="Скопировать текст отпечатка" />
        <Button variant="outline" asChild>
          <Link to={`/loyalty/draw/print/${draw.draw_id}`}>
            <Printer className="h-4 w-4 mr-2" />
            Печатный список
          </Link>
        </Button>
      </div>
    </section>
  )
}
