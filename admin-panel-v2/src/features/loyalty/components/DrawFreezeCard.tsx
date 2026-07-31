import { useState } from 'react'
import { Snowflake } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { ConfirmDialog } from '@/shared/components/common/ConfirmDialog'
import { useCommitDraw } from '../hooks/useLoyalty'
import { serverMessage } from '../api/serverMessage'

interface DrawFreezeCardProps {
  eligibleTickets: number
  cleanTickets: number
  triggerLabel?: string
}

/** The freeze action with the ratified mechanical guard: the administrator types the number they
 * are about to freeze. The number on screen is the POST-FILTER one — the same the freeze takes,
 * so a correct press passes on the first try; a stale number fails with the server's count.
 * At zero the button is disabled outright — `expected_tickets` starts at 1 (Гейт-1, уточнение 3). */
export const DrawFreezeCard = ({
  eligibleTickets,
  cleanTickets,
  triggerLabel = 'Заморозить',
}: DrawFreezeCardProps) => {
  const [dialogOpen, setDialogOpen] = useState(false)
  const [conductedOn, setConductedOn] = useState('')
  const commit = useCommitDraw()

  const handleConfirm = () => {
    commit.mutate(
      {
        expected_tickets: eligibleTickets,
        ...(conductedOn ? { conducted_on: conductedOn } : {}),
      },
      { onSuccess: () => setDialogOpen(false) }
    )
  }

  return (
    <section className="border rounded-lg p-6 space-y-4">
      <div>
        <p className="text-lg font-semibold">К заморозке готово {eligibleTickets} билетов</p>
        <p className="text-sm text-muted-foreground">
          Всего чистых билетов: {cleanTickets}
          {cleanTickets !== eligibleTickets &&
            ' — часть владельцев не пройдёт перепроверку пригодности и в тираж не попадёт'}
        </p>
      </div>

      <div className="flex items-end gap-3">
        <div className="space-y-1">
          <label htmlFor="conducted-on" className="text-sm text-muted-foreground">
            Дата проведения (необязательно)
          </label>
          <Input
            id="conducted-on"
            type="date"
            value={conductedOn}
            onChange={(event) => setConductedOn(event.target.value)}
            className="w-[180px]"
          />
        </div>
        <Button onClick={() => setDialogOpen(true)} disabled={eligibleTickets === 0}>
          <Snowflake className="h-4 w-4 mr-2" />
          {triggerLabel}
        </Button>
      </div>
      {eligibleTickets === 0 && (
        <p className="text-sm text-muted-foreground">Нет билетов к заморозке.</p>
      )}

      {commit.isError && (
        <p className="text-sm text-destructive">{serverMessage(commit.error)}</p>
      )}

      <ConfirmDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        title="Заморозить список участников?"
        description={`Замораживается ${eligibleTickets} билетов. Для подтверждения введите это число.`}
        confirmText="Заморозить"
        confirmValue={String(eligibleTickets)}
        onConfirm={handleConfirm}
        isLoading={commit.isPending}
      />
    </section>
  )
}
