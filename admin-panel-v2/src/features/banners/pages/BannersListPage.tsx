import { useState, useRef } from 'react'
import {
  Image as ImageIcon,
  Plus,
  Trash2,
  ChevronUp,
  ChevronDown,
  Eye,
  EyeOff,
  Pencil,
  Settings,
  Loader2,
  ExternalLink,
  Smartphone,
} from 'lucide-react'
import {
  useBanners,
  useCreateBanner,
  useUpdateBanner,
  useDeleteBanner,
  useUploadBannerImage,
  useUploadBannerVideo,
  useBannerInterval,
  useUpdateBannerInterval,
} from '../hooks/useBanners'
import type { Banner, BannerUpdateData } from '../types/banner.types'
import { BannerEditor } from '../components/BannerEditor'
import { formatBannerError } from '../model/bannerErrors'
import { getPhoneFormatStatus, hasPhonePicture, inspectPictureFile } from '../model/bannerMedia'
import { ConfirmDialog } from '@/shared/components/common/ConfirmDialog'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Card } from '@/shared/components/ui/card'
import { Badge } from '@/shared/components/ui/badge'
import { cn } from '@/shared/lib/utils'

interface PendingPhoneConfirm {
  title: string
  description: string
  confirmText: string
  action: () => Promise<void>
}

export function BannersListPage() {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [editedInterval, setEditedInterval] = useState<string | null>(null)
  const [editingBannerId, setEditingBannerId] = useState<number | null>(null)
  const [pageError, setPageError] = useState<string | null>(null)
  const [createNotice, setCreateNotice] = useState<string | null>(null)
  const [pendingPhoneConfirm, setPendingPhoneConfirm] = useState<PendingPhoneConfirm | null>(null)

  // Queries
  const { data, isLoading, error } = useBanners()
  const { data: intervalData } = useBannerInterval()

  // Mutations
  const createBanner = useCreateBanner()
  const updateBanner = useUpdateBanner()
  const deleteBanner = useDeleteBanner()
  const uploadImage = useUploadBannerImage()
  const uploadVideo = useUploadBannerVideo()
  const updateInterval = useUpdateBannerInterval()

  // Sort banners by sort_order
  const banners = data?.items?.slice().sort((a, b) => a.sort_order - b.sort_order) || []
  const intervalValue = editedInterval ?? intervalData?.value ?? ''
  const phoneFormat = getPhoneFormatStatus(banners)

  // Handle file upload and create banner hidden (A-05)
  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return

    setPageError(null)
    setCreateNotice(null)

    const check = await inspectPictureFile('wide', file, false)
    if (!check.ok) {
      setPageError(check.error)
      return
    }

    try {
      const imageUrl = await uploadImage.mutateAsync(file)
      const created = await createBanner.mutateAsync({
        title: `Баннер ${banners.length + 1}`,
        image_url: imageUrl,
        link_url: '/catalog',
        sort_order: banners.length,
        is_active: false,
      })
      setCreateNotice(
        `Баннер «${created.title}» создан скрытым. Чтобы показать его посетителям, нажмите кнопку показа («глаз») в строке баннера.`
      )
    } catch (err) {
      setPageError(formatBannerError(err, 'Не удалось создать баннер.'))
    }
  }

  const updateBannerRecord = (id: number, payload: BannerUpdateData): Promise<Banner> =>
    updateBanner.mutateAsync({ id, data: payload })

  // Toggle banner active status with phone-format confirmation when turning off (A-07)
  const handleToggle = async (banner: Banner) => {
    setPageError(null)
    const nextActive = !banner.is_active

    if (phoneFormat.isOn && nextActive && !hasPhonePicture(banner)) {
      setPendingPhoneConfirm({
        title: `Показать баннер «${banner.title}» без фото 3:2?`,
        description:
          'Сейчас у всех активных баннеров есть фото 3:2. После показа этого баннера все телефоны вернутся к узкой полосе 3:1, пока у этого баннера не появится картинка 3:2.',
        confirmText: 'Всё равно показать',
        action: async () => {
          try {
            await updateBanner.mutateAsync({ id: banner.id, data: { is_active: true } })
          } catch (err) {
            setPageError(formatBannerError(err, 'Не удалось изменить видимость баннера.'))
          }
        },
      })
      return
    }

    try {
      await updateBanner.mutateAsync({
        id: banner.id,
        data: { is_active: nextActive },
      })
    } catch (err) {
      setPageError(formatBannerError(err, 'Не удалось изменить видимость баннера.'))
    }
  }

  // Update link URL
  const handleLinkChange = async (banner: Banner, linkUrl: string) => {
    if (linkUrl === (banner.link_url ?? '')) return
    setPageError(null)
    try {
      await updateBanner.mutateAsync({
        id: banner.id,
        data: { link_url: linkUrl || null },
      })
    } catch (err) {
      setPageError(formatBannerError(err, 'Не удалось сохранить ссылку баннера.'))
    }
  }

  // Move banner up/down
  const handleMove = async (banner: Banner, direction: 'up' | 'down') => {
    const currentIndex = banners.findIndex((b) => b.id === banner.id)
    const newIndex = direction === 'up' ? currentIndex - 1 : currentIndex + 1

    if (newIndex < 0 || newIndex >= banners.length) return

    const otherBanner = banners[newIndex]
    if (!otherBanner) return

    setPageError(null)
    try {
      await Promise.all([
        updateBanner.mutateAsync({
          id: banner.id,
          data: { sort_order: newIndex },
        }),
        updateBanner.mutateAsync({
          id: otherBanner.id,
          data: { sort_order: currentIndex },
        }),
      ])
    } catch (err) {
      setPageError(formatBannerError(err, 'Не удалось изменить порядок баннеров.'))
    }
  }

  // Delete banner
  const handleDelete = async (banner: Banner) => {
    if (!window.confirm(`Удалить баннер "${banner.title}"?`)) return
    setPageError(null)
    try {
      await deleteBanner.mutateAsync(banner.id)
      if (editingBannerId === banner.id) {
        setEditingBannerId(null)
      }
    } catch (err) {
      setPageError(formatBannerError(err, 'Не удалось удалить баннер.'))
    }
  }

  // Save interval setting
  const handleSaveInterval = async () => {
    if (!intervalValue || intervalValue === intervalData?.value) return
    setPageError(null)
    try {
      await updateInterval.mutateAsync(intervalValue)
      setEditedInterval(null)
    } catch (err) {
      setPageError(formatBannerError(err, 'Не удалось сохранить интервал слайдера.'))
    }
  }

  const isUploading = uploadImage.isPending || createBanner.isPending

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Баннеры</h1>
          <p className="text-muted-foreground">
            Управление баннерами на главной странице ({banners.length})
          </p>
        </div>
        <div>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/jpeg,image/png,image/webp,image/gif,.jpg,.jpeg,.png,.webp,.gif"
            aria-label="Загрузить изображение для нового баннера"
            onChange={handleFileUpload}
            className="hidden"
          />
          <Button
            onClick={() => fileInputRef.current?.click()}
            disabled={isUploading}
          >
            {isUploading ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <Plus className="mr-2 h-4 w-4" />
            )}
            Добавить баннер
          </Button>
        </div>
      </div>

      {/* Interval Settings */}
      <Card className="p-4">
        <div className="flex items-center gap-4">
          <Settings className="h-5 w-5 text-muted-foreground" />
          <span className="font-medium">Интервал слайдера:</span>
          <Input
            type="number"
            value={intervalValue}
            onChange={(e) => setEditedInterval(e.target.value)}
            className="w-24"
            min={1000}
            max={10000}
            step={500}
          />
          <span className="text-muted-foreground">мс</span>
          <Button
            variant="secondary"
            size="sm"
            onClick={handleSaveInterval}
            disabled={updateInterval.isPending || !intervalValue || intervalValue === intervalData?.value}
          >
            {updateInterval.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              'Сохранить'
            )}
          </Button>
        </div>
      </Card>

      {/* Phone Format Status (A-06) */}
      {!isLoading && !error && (
        <Card className="p-4 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <Smartphone className="h-5 w-5 text-muted-foreground" />
            <span className="font-medium">Формат 3:2 на телефонах:</span>
            <Badge variant={phoneFormat.isOn ? 'default' : 'secondary'}>
              {phoneFormat.isOn ? 'Включён' : 'Выключен'}
            </Badge>
          </div>
          {phoneFormat.isOn ? (
            <p className="text-sm text-muted-foreground">
              У всех активных баннеров ({phoneFormat.activeBanners.length}) загружено фото 3:2. На телефонах карусель показывает формат 3:2.
            </p>
          ) : phoneFormat.activeBanners.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              Нет ни одного активного баннера.
            </p>
          ) : (
            <div className="space-y-1.5 text-sm text-muted-foreground">
              <p>
                На телефонах показывается узкая полоса 3:1, пока не загружено фото 3:2 для каждого активного баннера. Не хватает фото 3:2 у:
              </p>
              <ul className="list-disc pl-5 space-y-0.5 text-foreground">
                {phoneFormat.missingPhoneBanners.map((b) => (
                  <li key={b.id}>#{b.id} — {b.title}</li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      )}

      {createNotice && (
        <div
          role="status"
          className="rounded-lg border border-sky-300 bg-sky-50 p-3 text-sm text-sky-900 dark:border-sky-800 dark:bg-sky-950/30 dark:text-sky-200"
        >
          {createNotice}
        </div>
      )}

      {pageError && (
        <div
          role="alert"
          className="rounded-lg border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive"
        >
          {pageError}
        </div>
      )}

      {/* Banners List */}
      <Card>
        {isLoading ? (
          <div className="p-8 text-center">
            <Loader2 className="h-8 w-8 animate-spin mx-auto mb-2" />
            <p className="text-muted-foreground">Загрузка...</p>
          </div>
        ) : error ? (
          <div className="p-8 text-center text-destructive">
            Ошибка загрузки баннеров
          </div>
        ) : banners.length === 0 ? (
          <div className="p-8 text-center">
            <ImageIcon className="h-12 w-12 mx-auto mb-4 text-muted-foreground" />
            <p className="text-muted-foreground">Нет баннеров</p>
            <p className="text-sm text-muted-foreground mt-1">
              Нажмите "Добавить баннер" чтобы загрузить первый
            </p>
          </div>
        ) : (
          <div className="divide-y">
            {banners.map((banner, index) => {
              const isEditing = editingBannerId === banner.id
              const missingPhone = !hasPhonePicture(banner)

              return (
                <div key={banner.id} className="p-4">
                  <div
                    className={cn(
                      'flex flex-wrap items-center gap-4 transition-opacity',
                      !banner.is_active && 'opacity-60'
                    )}
                  >
                    {/* Preview */}
                    <div className="w-48 h-24 rounded-lg overflow-hidden bg-muted shrink-0">
                      {banner.image_url ? (
                        <img
                          src={banner.image_url}
                          alt={banner.title}
                          className="w-full h-full object-cover"
                        />
                      ) : (
                        <div className="w-full h-full flex items-center justify-center">
                          <ImageIcon className="h-8 w-8 text-muted-foreground" />
                        </div>
                      )}
                    </div>

                    {/* Info */}
                    <div className="flex-1 min-w-[220px]">
                      <div className="flex flex-wrap items-center gap-2 mb-2">
                        <span className="font-medium">{banner.title}</span>
                        <Badge variant={banner.is_active ? 'default' : 'secondary'}>
                          {banner.is_active ? 'Активен' : 'Скрыт'}
                        </Badge>
                        {missingPhone ? (
                          <Badge variant="outline">Нет фото 3:2</Badge>
                        ) : (
                          <Badge variant="secondary">Фото 3:2</Badge>
                        )}
                        {banner.video_url && <Badge variant="secondary">Видео</Badge>}
                      </div>
                      <div className="flex items-center gap-2">
                        <ExternalLink className="h-4 w-4 text-muted-foreground shrink-0" />
                        <Input
                          type="text"
                          defaultValue={banner.link_url || ''}
                          onBlur={(e) => handleLinkChange(banner, e.target.value)}
                          placeholder="Ссылка (например: /catalog)"
                          className="h-8 text-sm"
                        />
                      </div>
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-1 shrink-0">
                      <Button
                        type="button"
                        variant={isEditing ? 'secondary' : 'outline'}
                        size="sm"
                        onClick={() => setEditingBannerId(isEditing ? null : banner.id)}
                      >
                        <Pencil className="mr-1.5 h-3.5 w-3.5" />
                        {isEditing ? 'Свернуть' : 'Редактировать'}
                      </Button>

                      {/* Move up/down */}
                      <div className="flex flex-col">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          aria-label={`Переместить ${banner.title} выше`}
                          onClick={() => handleMove(banner, 'up')}
                          disabled={index === 0}
                        >
                          <ChevronUp className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          aria-label={`Переместить ${banner.title} ниже`}
                          onClick={() => handleMove(banner, 'down')}
                          disabled={index === banners.length - 1}
                        >
                          <ChevronDown className="h-4 w-4" />
                        </Button>
                      </div>

                      {/* Toggle visibility */}
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={banner.is_active ? `Скрыть ${banner.title}` : `Показать ${banner.title}`}
                        onClick={() => handleToggle(banner)}
                        title={banner.is_active ? 'Скрыть' : 'Показать'}
                      >
                        {banner.is_active ? (
                          <Eye className="h-4 w-4" />
                        ) : (
                          <EyeOff className="h-4 w-4" />
                        )}
                      </Button>

                      {/* Delete */}
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`Удалить ${banner.title}`}
                        onClick={() => handleDelete(banner)}
                        className="text-destructive hover:text-destructive"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>

                  {isEditing && (
                    <BannerEditor
                      key={banner.id}
                      banner={banner}
                      phoneFormatActive={phoneFormat.isOn}
                      onUpdate={updateBannerRecord}
                      onRequestPhoneFormatConfirm={(action, bannerTitle) => {
                        setPendingPhoneConfirm({
                          title: `Удалить фото 3:2 у баннера «${bannerTitle}»?`,
                          description:
                            'Все телефоны вернутся к узкой полосе 3:1, пока у этого баннера не появится картинка 3:2.',
                          confirmText: 'Сохранить без фото 3:2',
                          action,
                        })
                      }}
                      onClose={() => setEditingBannerId(null)}
                      uploadImage={(file) => uploadImage.mutateAsync(file)}
                      uploadVideo={(file) => uploadVideo.mutateAsync(file)}
                    />
                  )}
                </div>
              )
            })}
          </div>
        )}
      </Card>

      <ConfirmDialog
        open={pendingPhoneConfirm !== null}
        onOpenChange={(open) => {
          if (!open) setPendingPhoneConfirm(null)
        }}
        title={pendingPhoneConfirm?.title ?? ''}
        description={pendingPhoneConfirm?.description ?? ''}
        confirmText={pendingPhoneConfirm?.confirmText ?? 'Подтвердить'}
        variant="destructive"
        isLoading={updateBanner.isPending}
        onConfirm={() => {
          if (!pendingPhoneConfirm) return
          const action = pendingPhoneConfirm.action
          void action().finally(() => setPendingPhoneConfirm(null))
        }}
      />
    </div>
  )
}

export default BannersListPage
