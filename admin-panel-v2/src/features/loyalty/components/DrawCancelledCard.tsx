import { useState } from 'react'
import { Button } from '@/shared/components/ui/button'
import { Textarea } from '@/shared/components/ui/textarea'
import { apiClient } from '@/shared/api'
import { CopyTextButton } from './CopyTextButton'
import { cancelPostText, fingerprintPostText } from './drawTexts'
import { serverMessage } from '../api/serverMessage'
import type { CurrentDraw } from '../types'

/** The cancelled draw. The cancellation MUST be published the same way the fingerprint was —
 * a draw whose hash was announced and which then quietly disappears is indistinguishable from a
 * draw we did not like. The journal (the exact bytes the hash was taken over) is fetched on
 * demand through the authenticated client — a bare link would carry no token. */
export const DrawCancelledCard = ({ draw }: { draw: CurrentDraw }) => {
  const [journal, setJournal] = useState<string | null>(null)
  const [journalError, setJournalError] = useState<string | null>(null)

  const loadJournal = async () => {
    try {
      const response = await apiClient.get<string>(`/admin/lottery/${draw.draw_id}/export`, {
        responseType: 'text',
      })
      setJournal(response.data)
    } catch (error) {
      setJournalError(serverMessage(error))
    }
  }

  return (
    <section className="border rounded-lg p-6 space-y-4">
      <div>
        <h2 className="text-2xl font-bold">Розыгрыш №{draw.draw_number} отменён</h2>
        <p className="text-muted-foreground">
          Билеты возвращены участникам. Опубликуйте отмену тем же каналом, что и отпечаток.
        </p>
      </div>

      <div className="flex gap-3">
        <CopyTextButton text={cancelPostText(draw)} label="Скопировать текст отмены" />
        <CopyTextButton text={fingerprintPostText(draw)} label="Скопировать текст отпечатка" />
      </div>

      {journal === null ? (
        <Button variant="outline" onClick={loadJournal}>
          Показать журнал отмены
        </Button>
      ) : (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">
            Канонический список отменённого тиража — его хеш совпадает с опубликованным отпечатком:
          </p>
          <Textarea readOnly value={journal} rows={8} />
        </div>
      )}
      {journalError && <p className="text-sm text-destructive">{journalError}</p>}
    </section>
  )
}
