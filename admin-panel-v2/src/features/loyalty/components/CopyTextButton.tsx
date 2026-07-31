import { useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Textarea } from '@/shared/components/ui/textarea'

interface CopyTextButtonProps {
  text: string
  label: string
}

/** Copies the publication text for the channel post. The clipboard API exists only in a secure
 * context, and silently copying nothing is the worst outcome for a text that must reach the
 * channel byte-exact — so without it the text is shown, pre-selected, with a manual hint. */
export const CopyTextButton = ({ text, label }: CopyTextButtonProps) => {
  const [state, setState] = useState<'idle' | 'copied' | 'fallback'>('idle')

  const handleCopy = async () => {
    if (navigator.clipboard) {
      try {
        await navigator.clipboard.writeText(text)
        setState('copied')
        return
      } catch {
        // fall through to the manual path — a denied permission is not a reason to lose the text
      }
    }
    setState('fallback')
  }

  if (state === 'fallback') {
    return (
      <div className="space-y-2">
        <Textarea
          readOnly
          value={text}
          rows={5}
          onFocus={(event) => event.target.select()}
          autoFocus
        />
        <p className="text-sm text-muted-foreground">
          Буфер обмена недоступен — текст выделен, скопируйте вручную.
        </p>
      </div>
    )
  }

  return (
    <Button variant="outline" onClick={handleCopy}>
      {state === 'copied' ? <Check className="h-4 w-4 mr-2" /> : <Copy className="h-4 w-4 mr-2" />}
      {state === 'copied' ? 'Скопировано' : label}
    </Button>
  )
}
