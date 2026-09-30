import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@/shared/components/ui/button'
import { ConfirmDialog } from '@/shared/components/common/ConfirmDialog'
import { useCampaignActions } from '../hooks/useCampaignActions'
import { CampaignSummary } from './CampaignSummary'
import { formatCampaignDate, CAMPAIGN_ZONE } from '../model/campaignDates'
import type { CampaignRecord } from '../types/campaign.types'

const ACTION_LABELS = { create: 'Создание черновика', patch: 'Сохранение черновика', publish: 'Публикация', end: 'Завершение' }

export function CampaignRecoveryPanel({ onAdopt }: { onAdopt?: (record: CampaignRecord) => void }) {
  const { controller, state } = useCampaignActions()
  const [confirmation, setConfirmation] = useState(false)
  const navigate = useNavigate()
  if (state.status === 'idle') return null
  if (state.status === 'accessDenied') return <section role="alert" className="p-4 border rounded-lg space-y-3">
    <p>{state.message}</p><Button onClick={() => { void controller.authorize() }}>Проверить полномочия</Button>
  </section>
  if (state.status === 'pending') return <p role="status">Действие отправлено. Ожидайте подтверждения.</p>
  if (state.status === 'definiteError') return <p role="alert">{state.message}</p>
  const attempt = state.attempt
  const listRecovery = !attempt || attempt === 'corrupt' || attempt.kind === 'create'
  const checking = state.status === 'checking'
  const canResolve = state.status === 'awaitingDecision' && (!!state.record || !!state.page)
  const label = state.record ? (state.conflict ? 'Принять актуальную запись' : state.status === 'awaitingDecision' && attempt && attempt !== 'corrupt' && attempt.kind !== 'create' ?
    'Принять текущее состояние и продолжить' : `Использовать запись №${state.record.id} и закрыть проверку`) :
    attempt === 'corrupt' ? 'Закрыть повреждённую отметку' : 'Закрыть проверку и создать другой черновик'
  return <section className="border border-amber-500 rounded-lg p-4 space-y-4" aria-label="Проверка результата">
    <p role="alert">{state.message ?? 'Результат действия не подтверждён.'}</p>
    {attempt && attempt !== 'corrupt' && <p>Действие: {ACTION_LABELS[attempt.kind]} · {attempt.campaignId ? `№${attempt.campaignId}` : attempt.createName} ·
      отправлено {formatCampaignDate(attempt.sentAt)} · {CAMPAIGN_ZONE}{attempt.baseVersion && ` · исходная версия ${attempt.baseVersion}`}</p>}
    {attempt === 'corrupt' && <p>Отметка недоступна или повреждена. Предыдущее действие могло завершиться.</p>}
    <p>Проверка показывает текущее состояние, но не доказывает, кто выполнил действие. Автоматического повторения нет.</p>
    <Button variant="outline" disabled={checking} onClick={() => { void controller.inspect() }}>{listRecovery ? 'Проверить список' : 'Проверить запись'}</Button>
    {checking && <p role="status">Проверка…</p>}
    {state.record && <CampaignSummary record={state.record} />}
    {state.page && <div className="space-y-3">
      <p>Свежий список · Все · страница {state.page.page}. Совпадение названий и отсутствие записи не доказывают результат создания.</p>
      {state.page.items.map(record => <div key={record.id} className="border p-3 rounded-lg">
        <CampaignSummary record={record} />
        <Button variant="outline" onClick={() => { void controller.inspect(1, record.id) }}>Проверить запись №{record.id}</Button>
      </div>)}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" disabled={state.page.page <= 1} onClick={() => { void controller.inspect((state.page?.page ?? 1) - 1) }}>Предыдущая страница проверки</Button>
        <Button variant="outline" disabled={state.page.page >= state.page.pages} onClick={() => { void controller.inspect((state.page?.page ?? 1) + 1) }}>Следующая страница проверки</Button>
      </div>
    </div>}
    <Button disabled={!canResolve} onClick={() => setConfirmation(true)}>{label}</Button>
    <ConfirmDialog open={confirmation} onOpenChange={setConfirmation} title={label}
      description={state.record ? 'Принять показанную запись как новую основу. Несохранённые изменения будут потеряны. Это не подтверждает результат прежнего действия.' :
        'Первое действие могло завершиться и создать запись. Новый черновик может стать дубликатом. Ввод будет потерян; откроется пустая форма.'}
      onConfirm={() => {
        const record = controller.resolve()
        if (record === false) return
        setConfirmation(false)
        if (record) {
          if (onAdopt) onAdopt(record)
          navigate(`/campaigns/${record.id}`)
        } else navigate('/campaigns/new', { state: { newAttempt: crypto.randomUUID() } })
      }} />
  </section>
}
