import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Printer } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { ConfirmDialog } from '@/shared/components/common/ConfirmDialog'
import { useCancelDraw, useSetConductedOn } from '../hooks/useLoyalty'
import { serverMessage } from '../api/serverMessage'
import { CopyTextButton } from './CopyTextButton'
import { DrawWinnersForm } from './DrawWinnersForm'
import { fingerprintPostText } from './drawTexts'
import type { CurrentDraw } from '../types'

/** The live draw: fingerprint to publish, print list, announced date, winners, and the second
 * line of defence — cancellation. Nothing here recomputes anything: it all renders step-3 reads. */
export const DrawLiveCard = ({ draw }: { draw: CurrentDraw }) => {
  const [date, setDate] = useState(draw.conducted_on ?? '')
  const [cancelOpen, setCancelOpen] = useState(false)
  const setDate_ = useSetConductedOn(draw.draw_id)
  const cancel = useCancelDraw()

  return (
    <>
      <section className="border rounded-lg p-6 space-y-4">
        <div>
          <h2 className="text-2xl font-bold">
            Розыгрыш №{draw.draw_number} заморожен
          </h2>
          <p className="text-muted-foreground">Билетов: {draw.total_tickets}</p>
        </div>

        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">
            Отпечаток (SHA-256) — опубликуйте постом в канале до розыгрыша:
          </p>
          <code className="block text-xs break-all bg-muted p-2 rounded">
            {draw.participant_hash}
          </code>
          <div className="flex gap-3">
            <CopyTextButton text={fingerprintPostText(draw)} label="Скопировать текст отпечатка" />
            <Button variant="outline" asChild>
              <Link to={`/loyalty/draw/print/${draw.draw_id}`}>
                <Printer className="h-4 w-4 mr-2" />
                Печатный список
              </Link>
            </Button>
          </div>
        </div>

        <div className="flex items-end gap-3">
          <div className="space-y-1">
            <label htmlFor="edit-conducted-on" className="text-sm text-muted-foreground">
              Дата проведения
            </label>
            <Input
              id="edit-conducted-on"
              type="date"
              value={date}
              onChange={(event) => setDate(event.target.value)}
              className="w-[180px]"
            />
          </div>
          <Button
            variant="outline"
            onClick={() => setDate_.mutate(date || null)}
            disabled={setDate_.isPending || date === (draw.conducted_on ?? '')}
          >
            Сохранить дату
          </Button>
        </div>
        {setDate_.isError && (
          <p className="text-sm text-destructive">{serverMessage(setDate_.error)}</p>
        )}

        <div>
          <Button variant="destructive" onClick={() => setCancelOpen(true)}>
            Отменить тираж
          </Button>
          {cancel.isError && (
            <p className="text-sm text-destructive mt-2">{serverMessage(cancel.error)}</p>
          )}
        </div>

        <ConfirmDialog
          open={cancelOpen}
          onOpenChange={setCancelOpen}
          title={`Отменить розыгрыш №${draw.draw_number}?`}
          description="Все билеты вернутся участникам. Отмену придётся опубликовать тем же каналом, что и отпечаток."
          confirmText="Отменить тираж"
          variant="destructive"
          onConfirm={() => cancel.mutate(draw.draw_id, { onSuccess: () => setCancelOpen(false) })}
          isLoading={cancel.isPending}
        />
      </section>

      <DrawWinnersForm drawId={draw.draw_id} totalTickets={draw.total_tickets} />
    </>
  )
}
