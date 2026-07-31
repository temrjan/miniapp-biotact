import { useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/components/ui/dialog'

interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  confirmText?: string
  cancelText?: string
  variant?: 'default' | 'destructive'
  onConfirm: () => void
  isLoading?: boolean
  /** When set, the confirm button stays disabled until this exact value is typed.
   * The standard guard for irreversible actions ("type the number to confirm") —
   * without it the dialog behaves exactly as before. */
  confirmValue?: string
}

export const ConfirmDialog = (props: ConfirmDialogProps) => {
  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent>
        {/* Body holds the typed state: the portal unmounts it on close, so a half-typed
            value can never survive into the next opening — the guard is read every time. */}
        <ConfirmDialogBody {...props} />
      </DialogContent>
    </Dialog>
  )
}

const ConfirmDialogBody = ({
  onOpenChange,
  title,
  description,
  confirmText = 'Подтвердить',
  cancelText = 'Отмена',
  variant = 'default',
  onConfirm,
  isLoading = false,
  confirmValue,
}: ConfirmDialogProps) => {
  const [typed, setTyped] = useState('')

  const confirmBlocked = confirmValue !== undefined && typed.trim() !== confirmValue

  const handleConfirm = () => {
    onConfirm()
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2">
          {variant === 'destructive' && <AlertTriangle className="h-5 w-5 text-destructive" />}
          {title}
        </DialogTitle>
        <DialogDescription>{description}</DialogDescription>
      </DialogHeader>
      {confirmValue !== undefined && (
        <Input
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          placeholder={confirmValue}
          autoFocus
          disabled={isLoading}
        />
      )}
      <DialogFooter>
        <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isLoading}>
          {cancelText}
        </Button>
        <Button variant={variant} onClick={handleConfirm} disabled={isLoading || confirmBlocked}>
          {isLoading ? 'Загрузка...' : confirmText}
        </Button>
      </DialogFooter>
    </>
  )
}
