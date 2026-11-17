import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, ExternalLink, Image as ImageIcon, Loader2, Save, Trash2, Upload, Video } from 'lucide-react'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Label } from '@/shared/components/ui/label'
import { Textarea } from '@/shared/components/ui/textarea'
import type { Banner, BannerUpdateData } from '../types/banner.types'
import { formatBannerError } from '../model/bannerErrors'
import {
  getWideImageDimensionWarning,
  hasPhonePicture,
  inspectClipFile,
  inspectPictureFile,
  SUBTITLE_MAX_LENGTH,
  TITLE_MAX_LENGTH,
  type MediaDimensions,
} from '../model/bannerMedia'

type MediaSlotKey = 'image_url' | 'video_url' | 'image_mobile_url' | 'video_mobile_url'
type TextFieldKey = 'title' | 'title_uz' | 'subtitle' | 'subtitle_uz'

interface BannerDraft {
  title: string
  title_uz: string
  subtitle: string
  subtitle_uz: string
  image_url: string | null
  video_url: string | null
  image_mobile_url: string | null
  video_mobile_url: string | null
}

const TEXT_FIELDS: readonly {
  key: TextFieldKey
  idSuffix: string
  label: string
  maxLength: number
  multiline: boolean
  required: boolean
}[] = [
  { key: 'title', idSuffix: 'title', label: 'Заголовок (RU)', maxLength: TITLE_MAX_LENGTH, multiline: false, required: true },
  { key: 'title_uz', idSuffix: 'title-uz', label: 'Заголовок (UZ)', maxLength: TITLE_MAX_LENGTH, multiline: false, required: false },
  { key: 'subtitle', idSuffix: 'subtitle', label: 'Подзаголовок (RU)', maxLength: SUBTITLE_MAX_LENGTH, multiline: true, required: false },
  { key: 'subtitle_uz', idSuffix: 'subtitle-uz', label: 'Подзаголовок (UZ)', maxLength: SUBTITLE_MAX_LENGTH, multiline: true, required: false },
]

function draftFromBanner(banner: Banner): BannerDraft {
  return {
    title: banner.title,
    title_uz: banner.title_uz ?? '',
    subtitle: banner.subtitle ?? '',
    subtitle_uz: banner.subtitle_uz ?? '',
    image_url: banner.image_url ?? null,
    video_url: banner.video_url ?? null,
    image_mobile_url: banner.image_mobile_url ?? null,
    video_mobile_url: banner.video_mobile_url ?? null,
  }
}

function normalizeOptionalText(value: string): string | null {
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

function normalizeStoredOptional(value: string | null | undefined): string | null {
  return typeof value === 'string' ? normalizeOptionalText(value) : null
}

function buildBannerPatch(draft: BannerDraft, baseline: Banner): BannerUpdateData {
  const patch: BannerUpdateData = {}
  if (draft.title !== baseline.title) patch.title = draft.title.trim()

  for (const key of ['title_uz', 'subtitle', 'subtitle_uz'] as const) {
    const nextVal = normalizeOptionalText(draft[key])
    if (nextVal !== normalizeStoredOptional(baseline[key])) patch[key] = nextVal
  }

  for (const key of ['image_url', 'image_mobile_url', 'video_url', 'video_mobile_url'] as const) {
    if (draft[key] !== (baseline[key] ?? null)) patch[key] = draft[key]
  }

  return patch
}

interface MediaSlotCardProps {
  kind: 'image' | 'video'
  title: string
  requirement: string
  url: string | null
  accept: string
  inputAriaLabel: string
  previewAriaLabel: string
  addLabel: string
  replaceLabel: string
  removeLabel: string | null
  disabledReason: string | null
  isUploading: boolean
  busy: boolean
  warnings: string[]
  onSelectFile: (file: File) => void
  onRemove?: () => void
  onImageLoad?: (e: React.SyntheticEvent<HTMLImageElement>) => void
}

function MediaSlotCard({
  kind,
  title,
  requirement,
  url,
  accept,
  inputAriaLabel,
  previewAriaLabel,
  addLabel,
  replaceLabel,
  removeLabel,
  disabledReason,
  isUploading,
  busy,
  warnings,
  onSelectFile,
  onRemove,
  onImageLoad,
}: MediaSlotCardProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const slotDisabled = busy || disabledReason !== null

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (file) onSelectFile(file)
  }

  return (
    <section aria-label={title} className="rounded-lg border bg-background p-3 space-y-3">
      <div>
        <p className="text-sm font-medium">{title}</p>
        <p className="text-xs text-muted-foreground">{requirement}</p>
      </div>

      {url ? (
        kind === 'image' ? (
          <img
            src={url}
            alt={previewAriaLabel}
            onLoad={onImageLoad}
            className="w-full h-36 rounded border bg-muted object-cover"
          />
        ) : (
          <video
            src={url}
            controls
            muted
            playsInline
            ref={(el) => {
              if (el) el.muted = true
            }}
            aria-label={previewAriaLabel}
            className="w-full h-36 rounded border bg-black object-contain"
          />
        )
      ) : (
        <div className="w-full h-24 rounded border border-dashed bg-muted/40 flex flex-col items-center justify-center text-xs text-muted-foreground gap-1 px-3 text-center">
          {kind === 'image' ? <ImageIcon className="h-5 w-5" /> : <Video className="h-5 w-5" />}
          <span>{disabledReason ?? 'Файл не прикреплён'}</span>
        </div>
      )}

      {url && disabledReason && (
        <p className="text-xs text-muted-foreground rounded bg-muted p-2">{disabledReason}</p>
      )}

      {warnings.map((warning, idx) => (
        <div
          key={idx}
          className="flex items-start gap-2 rounded border border-amber-300 bg-amber-50 p-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200"
        >
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
          <span>{warning}</span>
        </div>
      ))}

      <input
        ref={fileInputRef}
        type="file"
        accept={accept}
        aria-label={inputAriaLabel}
        disabled={slotDisabled}
        onChange={handleFileChange}
        className="hidden"
      />

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={slotDisabled}
          onClick={() => fileInputRef.current?.click()}
        >
          {isUploading ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <Upload className="mr-1.5 h-3.5 w-3.5" />}
          {isUploading ? 'Загрузка...' : url ? replaceLabel : addLabel}
        </Button>

        {removeLabel && url && onRemove && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={slotDisabled}
            onClick={onRemove}
            className="text-destructive hover:text-destructive"
          >
            <Trash2 className="mr-1.5 h-3.5 w-3.5" />
            {removeLabel}
          </Button>
        )}
      </div>
    </section>
  )
}

interface BannerEditorProps {
  banner: Banner
  phoneFormatActive: boolean
  onUpdate: (id: number, data: BannerUpdateData) => Promise<Banner>
  onRequestPhoneFormatConfirm: (confirmAction: () => Promise<void>, bannerTitle: string) => void
  onDirtyChange?: (dirty: boolean) => void
  onClose: () => void
  uploadImage: (file: File) => Promise<string>
  uploadVideo: (file: File) => Promise<string>
}

export function BannerEditor({
  banner,
  phoneFormatActive,
  onUpdate,
  onRequestPhoneFormatConfirm,
  onDirtyChange,
  onClose,
  uploadImage,
  uploadVideo,
}: BannerEditorProps) {
  const [baseline, setBaseline] = useState<Banner>(banner)
  const [draft, setDraft] = useState<BannerDraft>(() => draftFromBanner(banner))
  const [wideImageDimensions, setWideImageDimensions] = useState<MediaDimensions | null>(null)
  const [clipWarnings, setClipWarnings] = useState<{ video_url: string[]; video_mobile_url: string[] }>({
    video_url: [],
    video_mobile_url: [],
  })
  const [uploadingSlot, setUploadingSlot] = useState<MediaSlotKey | null>(null)
  const [saving, setSaving] = useState(false)
  const [editorError, setEditorError] = useState<string | null>(null)
  const [editorSuccess, setEditorSuccess] = useState<string | null>(null)

  const patch = buildBannerPatch(draft, baseline)
  const isDirty = Object.keys(patch).length > 0
  const busy = saving || uploadingSlot !== null

  useEffect(() => {
    onDirtyChange?.(isDirty)
  }, [isDirty, onDirtyChange])

  useEffect(() => {
    return () => {
      onDirtyChange?.(false)
    }
  }, [onDirtyChange])

  const wideWarning = getWideImageDimensionWarning(wideImageDimensions, Boolean(draft.video_url))
  const wideImageWarnings = wideWarning ? [wideWarning] : []

  const phoneClipDisabledReason =
    !draft.video_url && !draft.image_mobile_url
      ? 'Недоступен: сначала добавьте широкий ролик 3:1 и фото для телефонов 3:2.'
      : !draft.video_url
        ? 'Недоступен: сначала добавьте широкий ролик 3:1.'
        : !draft.image_mobile_url
          ? 'Недоступен: сначала добавьте фото для телефонов 3:2.'
          : null

  const phonePictureWarnings =
    phoneFormatActive && banner.is_active && hasPhonePicture(baseline) && draft.image_mobile_url === null
      ? ['После сохранения этот активный баннер останется без фото 3:2, и формат 3:2 на телефонах выключится.']
      : []

  const previewUrl = `https://new.biotact.uz/#home?preview_banner=${banner.id}&lang=ru`

  const handleSelectPicture = async (slot: 'image_url' | 'image_mobile_url', file: File) => {
    setEditorError(null)
    setEditorSuccess(null)
    const check = await inspectPictureFile(slot === 'image_url' ? 'wide' : 'phone', file)
    if (!check.ok) {
      setEditorError(check.error)
      return
    }

    setUploadingSlot(slot)
    try {
      const url = await uploadImage(file)
      if (slot === 'image_url') setWideImageDimensions(check.dimensions)
      setDraft((prev) => ({ ...prev, [slot]: url }))
    } catch (err) {
      setEditorError(formatBannerError(err, 'Не удалось загрузить изображение.'))
    } finally {
      setUploadingSlot(null)
    }
  }

  const handleSelectClip = async (slot: 'video_url' | 'video_mobile_url', file: File) => {
    setEditorError(null)
    setEditorSuccess(null)
    const check = await inspectClipFile(slot === 'video_url' ? 'wide' : 'phone', file)
    if (!check.ok) {
      setEditorError(check.error)
      return
    }

    setUploadingSlot(slot)
    try {
      const url = await uploadVideo(file)
      setClipWarnings((prev) => ({ ...prev, [slot]: check.warnings }))
      setDraft((prev) => ({ ...prev, [slot]: url }))
    } catch (err) {
      setEditorError(formatBannerError(err, 'Не удалось загрузить видеоролик.'))
    } finally {
      setUploadingSlot(null)
    }
  }

  const handleRemoveSlot = (slot: 'video_url' | 'image_mobile_url' | 'video_mobile_url') => {
    setEditorError(null)
    setEditorSuccess(null)
    setDraft((prev) => ({
      ...prev,
      [slot]: null,
      ...(slot !== 'video_mobile_url' ? { video_mobile_url: null } : {}),
    }))
    setClipWarnings((prev) => ({
      video_url: slot === 'video_url' ? [] : prev.video_url,
      video_mobile_url: [],
    }))
  }

  const executeSave = async (nextPatch: BannerUpdateData) => {
    setSaving(true)
    setEditorError(null)
    setEditorSuccess(null)
    try {
      const updated = await onUpdate(banner.id, nextPatch)
      setBaseline(updated)
      setDraft(draftFromBanner(updated))
      setEditorSuccess('Изменения сохранены.')
    } catch (err) {
      setEditorError(formatBannerError(err, 'Не удалось сохранить изменения баннера.'))
    } finally {
      setSaving(false)
    }
  }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setEditorError(null)
    setEditorSuccess(null)

    if (!draft.title.trim()) {
      setEditorError('Заголовок (RU) не может быть пустым.')
      return
    }
    if (draft.title.length > TITLE_MAX_LENGTH || draft.title_uz.length > TITLE_MAX_LENGTH) {
      setEditorError(`Заголовок не может превышать ${TITLE_MAX_LENGTH} символов.`)
      return
    }
    if (draft.subtitle.length > SUBTITLE_MAX_LENGTH || draft.subtitle_uz.length > SUBTITLE_MAX_LENGTH) {
      setEditorError(`Подзаголовок не может превышать ${SUBTITLE_MAX_LENGTH} символов.`)
      return
    }

    const nextPatch = buildBannerPatch(draft, baseline)
    if (Object.keys(nextPatch).length === 0) return

    const willTurnOffPhoneFormat =
      phoneFormatActive &&
      banner.is_active &&
      hasPhonePicture(baseline) &&
      draft.image_mobile_url === null

    if (willTurnOffPhoneFormat) {
      onRequestPhoneFormatConfirm(() => executeSave(nextPatch), draft.title.trim() || banner.title)
      return
    }

    await executeSave(nextPatch)
  }

  const imageAccept = 'image/jpeg,image/png,image/webp,image/gif,.jpg,.jpeg,.png,.webp,.gif'

  return (
    <form onSubmit={handleSave} className="mt-4 border-t pt-4 space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-base font-semibold">Редактор баннера #{banner.id}</h3>
        {isDirty && (
          <span className="text-xs font-medium text-amber-700 dark:text-amber-300">
            Есть несохранённые изменения
          </span>
        )}
      </div>

      {/* Preview Link (A-09) */}
      <div className="rounded-md border bg-muted/40 p-3 text-sm flex flex-wrap items-center justify-between gap-2">
        <span className="text-muted-foreground">
          Предпросмотр на сайте (работает и для скрытого баннера; перед проверкой сохраните изменения):
        </span>
        <a
          href={previewUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 font-medium text-primary hover:underline break-all"
        >
          {previewUrl}
          <ExternalLink className="h-3.5 w-3.5 shrink-0" />
        </a>
      </div>

      {/* Bilingual Texts */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {TEXT_FIELDS.map(({ key, idSuffix, label, maxLength, multiline, required }) => {
          const inputId = `banner-${banner.id}-${idSuffix}`
          const value = draft[key]
          const onChange = (nextValue: string) => {
            setEditorSuccess(null)
            setDraft((prev) => ({ ...prev, [key]: nextValue }))
          }
          return (
            <div key={key} className="space-y-1.5">
              <div className="flex items-center justify-between">
                <Label htmlFor={inputId}>
                  {label} {required && <span className="text-destructive">*</span>}
                </Label>
                <span className="text-xs text-muted-foreground">
                  {value.length}/{maxLength}
                </span>
              </div>
              {multiline ? (
                <Textarea
                  id={inputId}
                  rows={2}
                  value={value}
                  maxLength={maxLength}
                  disabled={busy}
                  onChange={(e) => onChange(e.target.value)}
                />
              ) : (
                <Input
                  id={inputId}
                  value={value}
                  maxLength={maxLength}
                  disabled={busy}
                  onChange={(e) => onChange(e.target.value)}
                />
              )}
            </div>
          )
        })}
      </div>

      {draft.video_url && (
        <p className="text-xs text-muted-foreground">
          Для баннера с видео обязательны «Заголовок (RU)» и «Заголовок (UZ)». Если заполнен «Подзаголовок (RU)», нужен и «Подзаголовок (UZ)».
        </p>
      )}

      {/* Pre-upload check scope note (A-02) */}
      <div className="rounded-md border bg-muted/30 p-3 text-xs text-muted-foreground">
        Все файлы загружаются готовыми (кадр из ролика админка не вырезает). Перед загрузкой проверяются размер, длительность и разрешение кадра. Кодек (H.264), отсутствие звуковой дорожки и fast start (moov перед mdat) админка не проверяет — проверьте их заранее скриптом <code>check_clip.py</code>.
      </div>

      {/* Four Media Slots */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <MediaSlotCard
          kind="image"
          title="Широкое фото 3:1"
          requirement="Для видео-баннера рекомендуется 1920×640, до 5 МБ"
          url={draft.image_url}
          accept={imageAccept}
          inputAriaLabel="Файл широкого фото 3:1"
          previewAriaLabel={`Широкое фото 3:1 баннера ${banner.id}`}
          addLabel="Выбрать фото 3:1"
          replaceLabel="Заменить фото 3:1"
          removeLabel={null}
          disabledReason={null}
          isUploading={uploadingSlot === 'image_url'}
          busy={busy}
          warnings={wideImageWarnings}
          onSelectFile={(file) => {
            void handleSelectPicture('image_url', file)
          }}
          onImageLoad={(e) => {
            if (e.currentTarget.naturalWidth > 0 && e.currentTarget.naturalHeight > 0) {
              setWideImageDimensions({
                width: e.currentTarget.naturalWidth,
                height: e.currentTarget.naturalHeight,
              })
            }
          }}
        />

        <MediaSlotCard
          kind="video"
          title="Широкий ролик 3:1"
          requirement="MP4, строго 1920×640, 4–8 с (макс. 10 с), цель до 1,5 МБ (макс. 5 МБ)"
          url={draft.video_url}
          accept=".mp4,video/mp4"
          inputAriaLabel="Файл широкого ролика 3:1"
          previewAriaLabel={`Широкий ролик 3:1 баннера ${banner.id}`}
          addLabel="Добавить широкий ролик"
          replaceLabel="Заменить широкий ролик"
          removeLabel="Удалить широкий ролик"
          disabledReason={null}
          isUploading={uploadingSlot === 'video_url'}
          busy={busy}
          warnings={clipWarnings.video_url}
          onSelectFile={(file) => {
            void handleSelectClip('video_url', file)
          }}
          onRemove={() => handleRemoveSlot('video_url')}
        />

        <MediaSlotCard
          kind="image"
          title="Фото для телефонов 3:2"
          requirement="Строго 1080×720 (3:2), до 5 МБ"
          url={draft.image_mobile_url}
          accept={imageAccept}
          inputAriaLabel="Файл фото для телефонов 3:2"
          previewAriaLabel={`Фото для телефонов 3:2 баннера ${banner.id}`}
          addLabel="Добавить фото 3:2"
          replaceLabel="Заменить фото 3:2"
          removeLabel="Удалить фото 3:2"
          disabledReason={null}
          isUploading={uploadingSlot === 'image_mobile_url'}
          busy={busy}
          warnings={phonePictureWarnings}
          onSelectFile={(file) => {
            void handleSelectPicture('image_mobile_url', file)
          }}
          onRemove={() => handleRemoveSlot('image_mobile_url')}
        />

        <MediaSlotCard
          kind="video"
          title="Ролик для телефонов 3:2"
          requirement="MP4, строго 1080×720, 4–8 с (макс. 10 с), цель до 600 КБ (макс. 5 МБ)"
          url={draft.video_mobile_url}
          accept=".mp4,video/mp4"
          inputAriaLabel="Файл ролика для телефонов 3:2"
          previewAriaLabel={`Ролик для телефонов 3:2 баннера ${banner.id}`}
          addLabel="Добавить ролик 3:2"
          replaceLabel="Заменить ролик 3:2"
          removeLabel="Удалить мобильный ролик"
          disabledReason={phoneClipDisabledReason}
          isUploading={uploadingSlot === 'video_mobile_url'}
          busy={busy}
          warnings={clipWarnings.video_mobile_url}
          onSelectFile={(file) => {
            void handleSelectClip('video_mobile_url', file)
          }}
          onRemove={() => handleRemoveSlot('video_mobile_url')}
        />
      </div>

      {editorError && (
        <div role="alert" className="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive">
          {editorError}
        </div>
      )}

      {editorSuccess && (
        <div role="status" className="rounded-md border border-emerald-300 bg-emerald-50 p-3 text-sm text-emerald-900 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-200">
          {editorSuccess}
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        <Button type="button" variant="outline" onClick={onClose} disabled={saving}>
          Закрыть
        </Button>
        <Button type="submit" disabled={!isDirty || busy}>
          {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
          {saving ? 'Сохранение...' : 'Сохранить изменения'}
        </Button>
      </div>
    </form>
  )
}
