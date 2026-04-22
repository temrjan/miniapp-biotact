import { useState } from 'react'
import {
  Send,
  Plus,
  Ban,
  Loader2,
  Radio,
  CheckCircle,
  XCircle,
  Clock,
  AlertTriangle,
  FlaskConical,
} from 'lucide-react'
import {
  useBroadcasts,
  useBroadcast,
  useCreateBroadcast,
  useSendBroadcast,
  useTestSendBroadcast,
  useCancelBroadcast,
} from '../hooks/useBroadcasts'
import {
  getStatusLabel,
  getStatusVariant,
} from '../types/broadcast.types'
import type { Broadcast, BroadcastCreateData } from '../types/broadcast.types'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import { Textarea } from '@/shared/components/ui/textarea'
import { Card } from '@/shared/components/ui/card'
import { Badge } from '@/shared/components/ui/badge'
import { cn } from '@/shared/lib/utils'

// Form state
interface BroadcastFormState {
  title: string
  text: string
  photo_url: string
  video_url: string
  button_text: string
  button_url: string
}

const INITIAL_FORM: BroadcastFormState = {
  title: '',
  text: '',
  photo_url: '',
  video_url: '',
  button_text: '',
  button_url: '',
}

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case 'draft':
      return <Clock className="h-4 w-4" />
    case 'sending':
      return <Radio className="h-4 w-4 animate-pulse" />
    case 'completed':
      return <CheckCircle className="h-4 w-4" />
    case 'cancelled':
      return <Ban className="h-4 w-4" />
    case 'failed':
      return <XCircle className="h-4 w-4" />
    default:
      return <AlertTriangle className="h-4 w-4" />
  }
}

function ProgressBar({ broadcast }: { broadcast: Broadcast }) {
  if (broadcast.status !== 'sending' && broadcast.status !== 'completed') {
    return null
  }

  const total = broadcast.total_users || 1
  const processed = broadcast.sent_count + broadcast.failed_count + broadcast.blocked_count
  const percent = Math.min(Math.round((processed / total) * 100), 100)

  return (
    <div className="mt-2 space-y-1">
      <div className="h-2 bg-muted rounded-full overflow-hidden">
        <div
          className={cn(
            'h-full rounded-full transition-all duration-500',
            broadcast.status === 'completed' ? 'bg-green-500' : 'bg-primary'
          )}
          style={{ width: `${percent}%` }}
        />
      </div>
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>{percent}%</span>
        <span>
          {broadcast.sent_count} отпр. / {broadcast.blocked_count} блок. / {broadcast.failed_count} ош.
        </span>
      </div>
    </div>
  )
}

function SendingBroadcastTracker({ id }: { id: number }) {
  const { data: broadcast } = useBroadcast(id)

  if (!broadcast || broadcast.status !== 'sending') {
    return null
  }

  return <ProgressBar broadcast={broadcast} />
}

export function BroadcastsPage() {
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<BroadcastFormState>(INITIAL_FORM)
  const [formErrors, setFormErrors] = useState<string[]>([])

  // Queries
  const { data, isLoading, error } = useBroadcasts()

  // Mutations
  const createBroadcast = useCreateBroadcast()
  const sendBroadcast = useSendBroadcast()
  const testSendBroadcast = useTestSendBroadcast()
  const cancelBroadcast = useCancelBroadcast()

  const broadcasts = data?.items ?? []

  function validateForm(): string[] {
    const errors: string[] = []

    if (!form.title.trim()) {
      errors.push('Название обязательно')
    }
    if (!form.text.trim()) {
      errors.push('Текст сообщения обязателен')
    }
    if (form.photo_url && form.video_url) {
      errors.push('Нельзя одновременно указать фото и видео')
    }
    if ((form.photo_url || form.video_url) && form.text.length > 1024) {
      errors.push('Текст с медиа не может быть длиннее 1024 символов')
    }
    if ((form.button_text && !form.button_url) || (!form.button_text && form.button_url)) {
      errors.push('Текст кнопки и URL кнопки должны быть заполнены вместе')
    }

    return errors
  }

  async function handleCreate() {
    const errors = validateForm()
    if (errors.length > 0) {
      setFormErrors(errors)
      return
    }

    setFormErrors([])

    const payload: BroadcastCreateData = {
      title: form.title.trim(),
      text: form.text.trim(),
      photo_url: form.photo_url.trim() || null,
      video_url: form.video_url.trim() || null,
      button_text: form.button_text.trim() || null,
      button_url: form.button_url.trim() || null,
    }

    try {
      await createBroadcast.mutateAsync(payload)
      setForm(INITIAL_FORM)
      setShowForm(false)
    } catch (err: unknown) {
      if (err instanceof Error) {
        setFormErrors([err.message])
      }
    }
  }

  async function handleSend(broadcast: Broadcast) {
    if (!window.confirm(`Отправить рассылку "${broadcast.title}" всем пользователям?`)) {
      return
    }
    await sendBroadcast.mutateAsync(broadcast.id)
  }

  async function handleTestSend(broadcast: Broadcast) {
    try {
      await testSendBroadcast.mutateAsync(broadcast.id)
      window.alert('Тестовая отправка запущена — проверь свой Telegram')
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Ошибка тестовой отправки'
      window.alert(msg)
    }
  }

  async function handleCancel(broadcast: Broadcast) {
    if (!window.confirm(`Отменить рассылку "${broadcast.title}"?`)) {
      return
    }
    await cancelBroadcast.mutateAsync(broadcast.id)
  }

  function handleFormChange(field: keyof BroadcastFormState, value: string) {
    setForm((prev) => ({ ...prev, [field]: value }))
    if (formErrors.length > 0) {
      setFormErrors([])
    }
  }

  const textCharCount = form.text.length
  const textLimit = (form.photo_url || form.video_url) ? 1024 : 4096

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Рассылки</h1>
          <p className="text-muted-foreground">
            Массовая рассылка сообщений пользователям бота
          </p>
        </div>
        <Button onClick={() => setShowForm(!showForm)}>
          <Plus className="mr-2 h-4 w-4" />
          Новая рассылка
        </Button>
      </div>

      {/* Create Form */}
      {showForm && (
        <Card className="p-6 space-y-4">
          <h2 className="text-lg font-semibold">Новая рассылка</h2>

          {formErrors.length > 0 && (
            <div className="bg-destructive/10 text-destructive rounded-md p-3 text-sm space-y-1">
              {formErrors.map((err) => (
                <p key={err}>{err}</p>
              ))}
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="title">Название (только для админки)</Label>
            <Input
              id="title"
              value={form.title}
              onChange={(e) => handleFormChange('title', e.target.value)}
              placeholder="Например: Акция март 2026"
              maxLength={255}
            />
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="text">Текст сообщения</Label>
              <span className={cn(
                'text-xs',
                textCharCount > textLimit ? 'text-destructive' : 'text-muted-foreground'
              )}>
                {textCharCount} / {textLimit}
              </span>
            </div>
            <Textarea
              id="text"
              value={form.text}
              onChange={(e) => handleFormChange('text', e.target.value)}
              placeholder="Текст рассылки. **жирный** или HTML: <b>, <i>, <a>"
              rows={5}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="photo_url">URL фото (необязательно)</Label>
            <Input
              id="photo_url"
              value={form.photo_url}
              onChange={(e) => handleFormChange('photo_url', e.target.value)}
              placeholder="https://example.com/image.jpg"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="video_url">URL видео (необязательно, нельзя вместе с фото)</Label>
            <Input
              id="video_url"
              value={form.video_url}
              onChange={(e) => handleFormChange('video_url', e.target.value)}
              placeholder="Прямая ссылка на .mp4 (до 5MB)"
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="button_text">Текст кнопки (необязательно)</Label>
              <Input
                id="button_text"
                value={form.button_text}
                onChange={(e) => handleFormChange('button_text', e.target.value)}
                placeholder="Подробнее"
                maxLength={64}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="button_url">URL кнопки</Label>
              <Input
                id="button_url"
                value={form.button_url}
                onChange={(e) => handleFormChange('button_url', e.target.value)}
                placeholder="https://t.me/..."
              />
            </div>
          </div>

          <div className="flex gap-3 pt-2">
            <Button
              onClick={handleCreate}
              disabled={createBroadcast.isPending}
            >
              {createBroadcast.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Plus className="mr-2 h-4 w-4" />
              )}
              Создать черновик
            </Button>
            <Button
              variant="outline"
              onClick={() => {
                setShowForm(false)
                setForm(INITIAL_FORM)
                setFormErrors([])
              }}
            >
              Отмена
            </Button>
          </div>
        </Card>
      )}

      {/* Broadcasts List */}
      <Card>
        {isLoading ? (
          <div className="p-8 text-center">
            <Loader2 className="h-8 w-8 animate-spin mx-auto mb-2" />
            <p className="text-muted-foreground">Загрузка...</p>
          </div>
        ) : error ? (
          <div className="p-8 text-center text-destructive">
            Ошибка загрузки рассылок
          </div>
        ) : broadcasts.length === 0 ? (
          <div className="p-8 text-center">
            <Send className="h-12 w-12 mx-auto mb-4 text-muted-foreground" />
            <p className="text-muted-foreground">Нет рассылок</p>
            <p className="text-sm text-muted-foreground mt-1">
              Создайте первую рассылку нажав кнопку выше
            </p>
          </div>
        ) : (
          <div className="divide-y">
            {broadcasts.map((broadcast) => (
              <div key={broadcast.id} className="p-4 space-y-2">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <StatusIcon status={broadcast.status} />
                    <span className="font-medium">{broadcast.title}</span>
                    <Badge variant={getStatusVariant(broadcast.status)}>
                      {getStatusLabel(broadcast.status)}
                    </Badge>
                  </div>

                  <div className="flex items-center gap-2">
                    {broadcast.status === 'draft' && (
                      <>
                        <Button
                          size="sm"
                          variant="outline"
                          onClick={() => handleTestSend(broadcast)}
                          disabled={testSendBroadcast.isPending}
                          title="Отправить только себе — проверить как выглядит"
                        >
                          {testSendBroadcast.isPending ? (
                            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                          ) : (
                            <FlaskConical className="mr-1 h-3 w-3" />
                          )}
                          Тест (мне)
                        </Button>
                        <Button
                          size="sm"
                          onClick={() => handleSend(broadcast)}
                          disabled={sendBroadcast.isPending}
                        >
                          {sendBroadcast.isPending ? (
                            <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                          ) : (
                            <Send className="mr-1 h-3 w-3" />
                          )}
                          Отправить
                        </Button>
                      </>
                    )}
                    {broadcast.status === 'sending' && (
                      <Button
                        size="sm"
                        variant="destructive"
                        onClick={() => handleCancel(broadcast)}
                        disabled={cancelBroadcast.isPending}
                      >
                        {cancelBroadcast.isPending ? (
                          <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                        ) : (
                          <Ban className="mr-1 h-3 w-3" />
                        )}
                        Отменить
                      </Button>
                    )}
                  </div>
                </div>

                {/* Preview text */}
                <p className="text-sm text-muted-foreground line-clamp-2">
                  {broadcast.text}
                </p>

                {/* Meta info */}
                <div className="flex items-center gap-4 text-xs text-muted-foreground">
                  <span>
                    {new Date(broadcast.created_at).toLocaleString('ru-RU', {
                      day: '2-digit',
                      month: '2-digit',
                      year: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </span>
                  {broadcast.total_users > 0 && (
                    <span>Всего: {broadcast.total_users} польз.</span>
                  )}
                  {broadcast.photo_url && (
                    <span>С фото</span>
                  )}
                  {broadcast.video_url && (
                    <span>С видео</span>
                  )}
                  {broadcast.button_text && (
                    <span>С кнопкой: {broadcast.button_text}</span>
                  )}
                </div>

                {/* Progress bar for sending/completed */}
                {broadcast.status === 'sending' ? (
                  <SendingBroadcastTracker id={broadcast.id} />
                ) : (
                  <ProgressBar broadcast={broadcast} />
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}

export default BroadcastsPage
