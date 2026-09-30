import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Textarea } from '@/shared/components/ui/textarea'
import { ImageUploader } from '@/shared/components/upload/ImageUploader'
import { ConfirmDialog } from '@/shared/components/common/ConfirmDialog'
import type { CampaignRecord, DraftInput, EditInput } from '../types/campaign.types'
import { STATE_LABELS } from '../types/campaign.types'
import { EMPTY_VALUES, FormValidationError, formInput, isDirty, recordValues, validImageUrl } from '../model/campaignForm'
import type { CampaignValues } from '../model/campaignForm'
import { writesBlocked } from '../model/campaignRecovery'
import { CAMPAIGN_ZONE, formatCampaignDate } from '../model/campaignDates'
import { useCampaignActions } from '../hooks/useCampaignActions'
import { CampaignProductPicker } from './CampaignProductPicker'
import { CampaignRecoveryPanel } from './CampaignRecoveryPanel'
import { CampaignSummary } from './CampaignSummary'

interface FormProps { initial?: CampaignRecord; available: boolean }
export function CampaignForm({ initial, available }: FormProps) {
  const { controller, state, blocked } = useCampaignActions()
  const [saved, setSaved] = useState(initial)
  const [values, setValues] = useState(() => initial ? recordValues(initial) : { ...EMPTY_VALUES })
  const [uploading, setUploading] = useState(false)
  const [acceptedImage, setAcceptedImage] = useState(initial?.image_url ?? '')
  const [error, setError] = useState<string | null>(null)
  const [invalidFields, setInvalidFields] = useState<(keyof CampaignValues)[]>([])
  const [confirm, setConfirm] = useState<'publish' | 'end' | null>(null)
  const alive = useRef(true)
  const uploadActive = useRef(false)
  const navigate = useNavigate()
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const immutable = saved !== undefined && saved.state !== 'draft'
  const missing = state.errorCode === 'campaign_not_found' && state.attempt && state.attempt !== 'corrupt' && state.attempt.campaignId === saved?.id
  const disabled = immutable || blocked || !available || !!missing
  const dirty = saved ? isDirty(values, saved) : true
  const session = state.session
  const adopt = (record: CampaignRecord) => {
    setSaved(record); setValues(recordValues(record)); setAcceptedImage(record.image_url ?? ''); setError(null); setInvalidFields([])
  }
  const save = async () => {
    if (disabled || uploadActive.current) return
    try {
      const input = formInput(values, saved)
      if (!input) return
      setError(null); setInvalidFields([])
      const record = saved ? await controller.execute({ kind: 'patch', saved, input: input as EditInput }) :
        await controller.execute({ kind: 'create', input: input as DraftInput })
      if (record && alive.current && controller.isCurrent(session)) {
        adopt(record)
        if (!saved) navigate(`/campaigns/${record.id}`)
      }
    } catch (e: unknown) { setError(e instanceof Error ? e.message : 'Проверьте поля.'); setInvalidFields(e instanceof FormValidationError ? e.fields : ['start_at', 'end_at']) }
  }
  const serverFields: (keyof CampaignValues)[] = state.errorCode === 'invalid_campaign_dates' ? ['start_at', 'end_at'] :
    state.errorCode === 'invalid_image_url' ? ['image_url'] : state.errorCode?.startsWith('product_') ? ['product_id'] : []
  const fieldMessage = (key: keyof CampaignValues) => invalidFields.includes(key) ? error ?? undefined : serverFields.includes(key) ? state.message ?? undefined : undefined
  const fieldProps = (key: keyof CampaignValues) => ({ 'aria-invalid': !!fieldMessage(key), 'aria-describedby': fieldMessage(key) ? `campaign-${key}-error` : undefined })
  const fieldError = (key: keyof CampaignValues) => fieldMessage(key) && <p id={`campaign-${key}-error`} className="text-destructive">{fieldMessage(key)}</p>
  return <div className="space-y-5">
    <CampaignRecoveryPanel onAdopt={adopt} />
    {saved && <p>№{saved.id} · {STATE_LABELS[saved.state]} · Версия: {saved.updated_at}</p>}
    {error && <p role="alert">{error}</p>}
    <form className="space-y-5" onSubmit={e => { e.preventDefault(); void save() }}>
      <fieldset disabled={disabled || uploading} className="space-y-5 min-w-0">
        <label className="block" htmlFor="campaign-name">Название *</label>
        <Input id="campaign-name" {...fieldProps('name')} value={values.name} onChange={e => setValues(v => ({ ...v, name: e.target.value }))} />
        {fieldError('name')}
        <CampaignProductPicker value={values.product_id} onChange={value => setValues(v => ({ ...v, product_id: value }))} disabled={disabled || uploading} error={fieldMessage('product_id')} />
        <label className="block" htmlFor="campaign-image">Изображение · HTTPS URL загрузки магазина</label>
        <Input id="campaign-image" {...fieldProps('image_url')} value={values.image_url} onChange={e => setValues(v => ({ ...v, image_url: e.target.value }))} />
        {fieldError('image_url')}
        {acceptedImage && values.image_url === acceptedImage && validImageUrl(acceptedImage) && <img src={acceptedImage} alt="Изображение акции" className="max-h-48 max-w-full rounded-lg" />}
        {immutable && !values.image_url && <p>Изображение: Не задано</p>}
        {(['description', 'terms'] as const).map(key => <div key={key} className="space-y-2">
          <label htmlFor={`campaign-${key}`}>{key === 'description' ? 'Описание' : 'Условия'}</label>
          <Textarea id={`campaign-${key}`} value={values[key]} onChange={e => setValues(v => ({ ...v, [key]: e.target.value }))} />
        </div>)}
        {(['start_at', 'end_at'] as const).map(key => <div key={key} className="space-y-2">
          <label htmlFor={`campaign-${key}`}>{key === 'start_at' ? 'Начало' : 'Окончание'} · {CAMPAIGN_ZONE}</label>
          <Input id={`campaign-${key}`} {...fieldProps(key)} type="datetime-local" step="1" value={values[key]} onChange={e => {
            const input = e.target.value
            setValues(v => ({ ...v, [key]: input.length === 16 ? `${input}:00` : input }))
          }} />
          {fieldError(key)}
        </div>)}
      </fieldset>
      {!immutable && <>
        <section aria-label="Загрузка изображения" className="space-y-2">
          <p>Изображение акции · один файл, до 5 MB. Удаление из формы не удаляет файл магазина.</p>
          <ImageUploader maxFiles={1} value={values.image_url && values.image_url === acceptedImage ? [acceptedImage] : []}
            disabled={disabled} onUploadingChange={active => { uploadActive.current = active; setUploading(active) }}
            onChange={urls => {
              if (!alive.current || !controller.isCurrent(session) || writesBlocked(controller.getSnapshot())) return
              const image = urls[0] ?? ''
              if (image && !validImageUrl(image)) { setError('Загрузка вернула недопустимый URL.'); return }
              setValues(v => ({ ...v, image_url: image })); setAcceptedImage(image)
            }} />
        </section>
        <Button type="submit" disabled={disabled || uploading || !dirty}>Сохранить черновик</Button>
      </>}
    </form>
    {saved?.state === 'draft' && <div className="space-y-2">
      {dirty && <p>Сначала сохраните изменения. Публикация использует сохранённую запись.</p>}
      <Button disabled={blocked || !available || uploading || dirty || !!missing} onClick={() => setConfirm('publish')}>Опубликовать</Button>
    </div>}
    {saved?.state === 'published' && <Button disabled={blocked || !available || !!missing} variant="destructive" onClick={() => setConfirm('end')}>Завершить акцию</Button>}
    {saved && <div className="space-y-2">
      {immutable && <CampaignSummary record={saved} />}
      <p>Период · {CAMPAIGN_ZONE}: {formatCampaignDate(saved.start_at)} — {formatCampaignDate(saved.end_at)}</p>
      {state.errorCode === 'campaign_overlap' && <p role="alert">{state.message}</p>}
      <p>Опубликована: {formatCampaignDate(saved.published_at)} · {CAMPAIGN_ZONE}</p>
      <p>Завершена: {formatCampaignDate(saved.ended_at)} · {CAMPAIGN_ZONE}</p>
      {immutable && <p>Содержание неизменно. Исправление требует завершить акцию и создать новую запись.</p>}
    </div>}
    <ConfirmDialog open={confirm !== null} onOpenChange={open => { if (!open) setConfirm(null) }}
      title={confirm === 'end' ? 'Завершить акцию' : 'Опубликовать акцию'}
      description={saved ? `№${saved.id} · ${saved.name} · товар ${saved.product_id ?? 'Не задано'} · ${formatCampaignDate(saved.start_at)} — ${formatCampaignDate(saved.end_at)} · ${CAMPAIGN_ZONE}. ${confirm === 'end' ? 'Запись и история сохранятся.' : 'После публикации все поля станут неизменными.'}` : ''}
      isLoading={blocked || uploading || !available}
      onConfirm={() => {
        if (!saved || !confirm || blocked || missing || uploadActive.current || !available || (confirm === 'publish' && dirty)) return
        const kind = confirm
        setConfirm(null)
        void controller.execute({ kind, saved }).then(record => {
          if (record && alive.current && controller.isCurrent(session)) adopt(record)
        })
      }} />
  </div>
}
