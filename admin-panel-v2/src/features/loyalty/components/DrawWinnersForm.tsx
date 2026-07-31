import { useState } from 'react'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { useRecordWinners } from '../hooks/useLoyalty'
import { serverMessage } from '../api/serverMessage'

interface DrawWinnersFormProps {
  drawId: number
  totalTickets: number
}

const PRIZE_ORDINALS = ['Главный приз', '2-й приз', '3-й приз', '4-й приз']

/** Free-form numbers ("1, 2 3"), with the parsing SHOWN before submission: the interface's job is
 * to display how the input was understood, the server's job is to refuse — all-or-nothing, with a
 * message naming the problem. Hard field-count per prize is wrong: the prize fund is a marketing
 * decision that changes. */
export const DrawWinnersForm = ({ drawId, totalTickets }: DrawWinnersFormProps) => {
  const [raw, setRaw] = useState('')
  const record = useRecordWinners(drawId)

  const tokens = raw.split(/[\s,;]+/).filter((token) => token.length > 0)
  const numbers: number[] = []
  const invalid: string[] = []
  for (const token of tokens) {
    const parsed = Number(token)
    if (Number.isInteger(parsed) && parsed > 0) {
      numbers.push(parsed)
    } else {
      invalid.push(token)
    }
  }
  const outOfRange = numbers.filter((number) => number > totalTickets)

  const handleSubmit = () => {
    record.mutate(numbers, { onSuccess: () => setRaw('') })
  }

  return (
    <section className="border rounded-lg p-6 space-y-4">
      <h3 className="text-lg font-semibold">Победители</h3>
      <div className="space-y-1">
        <label htmlFor="winning-numbers" className="text-sm text-muted-foreground">
          Выигравшие номера через запятую или пробел — в порядке призов, первый номер забирает
          главный
        </label>
        <Input
          id="winning-numbers"
          value={raw}
          onChange={(event) => setRaw(event.target.value)}
          placeholder="например: 17, 4, 23"
        />
      </div>

      {numbers.length > 0 && (
        <ul className="text-sm space-y-1">
          {numbers.map((number, index) => (
            <li key={`${number}-${index}`}>
              {PRIZE_ORDINALS[index] ?? `${index + 1}-й приз`}: билет №{number}
            </li>
          ))}
        </ul>
      )}
      {invalid.length > 0 && (
        <p className="text-sm text-destructive">Не номера: {invalid.join(', ')}</p>
      )}
      {outOfRange.length > 0 && (
        <p className="text-sm text-destructive">
          Вне диапазона 1..{totalTickets}: {outOfRange.join(', ')}
        </p>
      )}
      {record.isError && <p className="text-sm text-destructive">{serverMessage(record.error)}</p>}

      <Button
        onClick={handleSubmit}
        disabled={numbers.length === 0 || invalid.length > 0 || record.isPending}
      >
        Записать победителей
      </Button>
    </section>
  )
}
