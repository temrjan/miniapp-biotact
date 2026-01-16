import { useState, useRef } from 'react'
import {
  Image as ImageIcon,
  Plus,
  Trash2,
  ChevronUp,
  ChevronDown,
  Eye,
  EyeOff,
  Settings,
  Loader2,
  ExternalLink,
} from 'lucide-react'
import {
  useBanners,
  useCreateBanner,
  useUpdateBanner,
  useDeleteBanner,
  useUploadBannerImage,
  useBannerInterval,
  useUpdateBannerInterval,
} from '../hooks/useBanners'
import type { Banner } from '../types/banner.types'
import { Button } from '@/shared/components/ui/button'
import { Input } from '@/shared/components/ui/input'
import { Card } from '@/shared/components/ui/card'
import { Badge } from '@/shared/components/ui/badge'
import { cn } from '@/shared/lib/utils'

export function BannersListPage() {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [intervalValue, setIntervalValue] = useState<string>('')

  // Queries
  const { data, isLoading, error } = useBanners()
  const { data: intervalData } = useBannerInterval()

  // Mutations
  const createBanner = useCreateBanner()
  const updateBanner = useUpdateBanner()
  const deleteBanner = useDeleteBanner()
  const uploadImage = useUploadBannerImage()
  const updateInterval = useUpdateBannerInterval()

  // Sort banners by sort_order
  const banners = data?.items?.slice().sort((a, b) => a.sort_order - b.sort_order) || []

  // Set interval value when data loads
  if (intervalData && !intervalValue) {
    setIntervalValue(intervalData.value)
  }

  // Handle file upload and create banner
  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    try {
      const imageUrl = await uploadImage.mutateAsync(file)
      await createBanner.mutateAsync({
        title: `Баннер ${banners.length + 1}`,
        image_url: imageUrl,
        link_url: '/catalog',
        sort_order: banners.length,
        is_active: true,
      })
    } catch (err) {
      console.error('Failed to create banner:', err)
    }

    // Reset input
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  // Toggle banner active status
  const handleToggle = async (banner: Banner) => {
    await updateBanner.mutateAsync({
      id: banner.id,
      data: { is_active: !banner.is_active },
    })
  }

  // Update link URL
  const handleLinkChange = async (banner: Banner, linkUrl: string) => {
    if (linkUrl === banner.link_url) return
    await updateBanner.mutateAsync({
      id: banner.id,
      data: { link_url: linkUrl || null },
    })
  }

  // Move banner up/down
  const handleMove = async (banner: Banner, direction: 'up' | 'down') => {
    const currentIndex = banners.findIndex((b) => b.id === banner.id)
    const newIndex = direction === 'up' ? currentIndex - 1 : currentIndex + 1

    if (newIndex < 0 || newIndex >= banners.length) return

    const otherBanner = banners[newIndex]

    // Swap sort_order values
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
  }

  // Delete banner
  const handleDelete = async (banner: Banner) => {
    if (!window.confirm(`Удалить баннер "${banner.title}"?`)) return
    await deleteBanner.mutateAsync(banner.id)
  }

  // Save interval setting
  const handleSaveInterval = async () => {
    if (!intervalValue || intervalValue === intervalData?.value) return
    await updateInterval.mutateAsync(intervalValue)
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
            accept="image/*"
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
            onChange={(e) => setIntervalValue(e.target.value)}
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
            disabled={updateInterval.isPending || intervalValue === intervalData?.value}
          >
            {updateInterval.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              'Сохранить'
            )}
          </Button>
        </div>
      </Card>

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
            {banners.map((banner, index) => (
              <div
                key={banner.id}
                className={cn(
                  'flex items-center gap-4 p-4 transition-opacity',
                  !banner.is_active && 'opacity-50'
                )}
              >
                {/* Preview */}
                <div className="w-48 h-24 rounded-lg overflow-hidden bg-muted flex-shrink-0">
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
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="font-medium">{banner.title}</span>
                    <Badge variant={banner.is_active ? 'default' : 'secondary'}>
                      {banner.is_active ? 'Активен' : 'Скрыт'}
                    </Badge>
                  </div>
                  <div className="flex items-center gap-2">
                    <ExternalLink className="h-4 w-4 text-muted-foreground flex-shrink-0" />
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
                <div className="flex items-center gap-1 flex-shrink-0">
                  {/* Move up/down */}
                  <div className="flex flex-col">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      onClick={() => handleMove(banner, 'up')}
                      disabled={index === 0}
                    >
                      <ChevronUp className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
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
                    onClick={() => handleDelete(banner)}
                    className="text-destructive hover:text-destructive"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}

export default BannersListPage
