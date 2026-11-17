import type { Banner } from '../types/banner.types'

export const MAX_MEDIA_BYTES = 5 * 1024 * 1024
export const WIDE_IMAGE_WIDTH = 1920
export const WIDE_IMAGE_HEIGHT = 640
export const PHONE_IMAGE_WIDTH = 1080
export const PHONE_IMAGE_HEIGHT = 720
export const CLIP_MIN_DURATION_S = 4
export const CLIP_MAX_DURATION_S = 8
export const CLIP_HARD_DURATION_S = 10
export const TITLE_MAX_LENGTH = 200
export const SUBTITLE_MAX_LENGTH = 500

export interface MediaDimensions {
  width: number
  height: number
}

export interface VideoMetadata extends MediaDimensions {
  duration: number
}

export type MediaCheckResult =
  | { ok: false; error: string }
  | { ok: true; dimensions: MediaDimensions; warnings: string[] }

const CLIP_SPECS = {
  wide: {
    width: WIDE_IMAGE_WIDTH,
    height: WIDE_IMAGE_HEIGHT,
    targetBytes: 1_500_000,
    targetLabel: '1,5 МБ (1 500 000 байт)',
    label: 'широкого ролика',
  },
  phone: {
    width: PHONE_IMAGE_WIDTH,
    height: PHONE_IMAGE_HEIGHT,
    targetBytes: 600_000,
    targetLabel: '600 КБ (600 000 байт)',
    label: 'ролика для телефонов',
  },
} as const

/** Return whether a banner carries a usable phone still image URL. */
export function hasPhonePicture(banner: Pick<Banner, 'image_mobile_url'>): boolean {
  return typeof banner.image_mobile_url === 'string' && banner.image_mobile_url.length > 0
}

/** Compute live 3:2 phone-format state and active banners missing a phone picture (A-06). */
export function getPhoneFormatStatus(banners: Banner[]) {
  const activeBanners = banners.filter((banner) => banner.is_active)
  const missingPhoneBanners = activeBanners.filter((banner) => !hasPhonePicture(banner))
  const isOn = activeBanners.length > 0 && missingPhoneBanners.length === 0
  return { isOn, activeBanners, missingPhoneBanners }
}

/** Format duration cleanly for user-facing Russian validation notices. */
function formatDuration(seconds: number): string {
  return Number.isInteger(seconds) ? String(seconds) : seconds.toFixed(1)
}

/** Format byte size for clip-size target warnings using decimal SI units matching clip-standard. */
function formatMetricSize(bytes: number): string {
  if (bytes >= 1_000_000) {
    return `${(bytes / 1_000_000).toFixed(2)} МБ`
  }
  return `${Math.round(bytes / 1000)} КБ`
}

/** Return the non-blocking dimension warning when a wide photo on a video banner is not 1920x640. */
export function getWideImageDimensionWarning(
  dimensions: MediaDimensions | null,
  hasWideClip: boolean
): string | null {
  if (!hasWideClip || !dimensions) return null
  if (dimensions.width === WIDE_IMAGE_WIDTH && dimensions.height === WIDE_IMAGE_HEIGHT) {
    return null
  }
  return `Предупреждение: размер широкого фото ${dimensions.width}×${dimensions.height}, а для баннера с видео рекомендуется ${WIDE_IMAGE_WIDTH}×${WIDE_IMAGE_HEIGHT}.`
}

function readImageDimensions(file: File): Promise<MediaDimensions> {
  return new Promise((resolve, reject) => {
    const objectUrl = URL.createObjectURL(file)
    const img = new Image()
    const cleanup = () => {
      img.onload = null
      img.onerror = null
      URL.revokeObjectURL(objectUrl)
    }
    img.onload = () => {
      const width = img.naturalWidth
      const height = img.naturalHeight
      cleanup()
      if (width <= 0 || height <= 0) {
        reject(new Error('Не удалось определить размеры изображения'))
        return
      }
      resolve({ width, height })
    }
    img.onerror = () => {
      cleanup()
      reject(new Error('Не удалось прочитать файл как изображение'))
    }
    img.src = objectUrl
  })
}

function readVideoMetadata(file: File): Promise<VideoMetadata> {
  return new Promise((resolve, reject) => {
    const objectUrl = URL.createObjectURL(file)
    const video = document.createElement('video')
    video.preload = 'metadata'
    const cleanup = () => {
      video.onloadedmetadata = null
      video.onerror = null
      video.removeAttribute('src')
      video.load()
      URL.revokeObjectURL(objectUrl)
    }
    video.onloadedmetadata = () => {
      const width = video.videoWidth
      const height = video.videoHeight
      const duration = video.duration
      cleanup()
      if (width <= 0 || height <= 0 || !Number.isFinite(duration) || duration <= 0) {
        reject(new Error('Не удалось прочитать характеристики видео'))
        return
      }
      resolve({ width, height, duration })
    }
    video.onerror = () => {
      cleanup()
      reject(new Error('Не удалось прочитать файл как видео'))
    }
    video.src = objectUrl
  })
}

/** Browser media probe wrapped in an object so unit tests can stub decoding without touching jsdom APIs. */
export const bannerMediaProbe = {
  readImageDimensions,
  readVideoMetadata,
}

/** Validate a selected MP4 clip file against clip-standard refuse/warn levels before upload (A-02). */
export async function inspectClipFile(kind: 'wide' | 'phone', file: File): Promise<MediaCheckResult> {
  const spec = CLIP_SPECS[kind]
  if (!file.name.toLowerCase().endsWith('.mp4')) {
    return { ok: false, error: 'Ролик должен быть файлом с расширением .mp4.' }
  }
  if (file.size > MAX_MEDIA_BYTES) {
    return { ok: false, error: 'Размер ролика превышает 5 МБ (5 МиБ). Загрузка отменена.' }
  }

  let metadata: VideoMetadata
  try {
    metadata = await bannerMediaProbe.readVideoMetadata(file)
  } catch {
    return { ok: false, error: 'Не удалось прочитать файл как видео. Проверьте, что файл является корректным MP4.' }
  }

  if (metadata.duration > CLIP_HARD_DURATION_S) {
    return {
      ok: false,
      error: `Длительность ролика (${formatDuration(metadata.duration)} с) превышает 10 с. Загрузка отменена.`,
    }
  }
  if (metadata.width !== spec.width || metadata.height !== spec.height) {
    return {
      ok: false,
      error: `Размер кадра ${spec.label} должен быть ${spec.width}×${spec.height}, получено ${metadata.width}×${metadata.height}.`,
    }
  }

  const warnings: string[] = []
  if (metadata.duration < CLIP_MIN_DURATION_S) {
    warnings.push(`Предупреждение: длительность ролика ${formatDuration(metadata.duration)} с (рекомендуется 4–8 с).`)
  } else if (metadata.duration > CLIP_MAX_DURATION_S) {
    warnings.push(`Предупреждение: длительность ролика ${formatDuration(metadata.duration)} с больше рекомендуемых 4–8 с (лимит 10 с).`)
  }
  if (file.size > spec.targetBytes) {
    warnings.push(`Предупреждение: размер файла ${formatMetricSize(file.size)} больше рекомендуемых ${spec.targetLabel}.`)
  }

  return {
    ok: true,
    dimensions: { width: metadata.width, height: metadata.height },
    warnings,
  }
}

/** Validate a selected still picture file against size and resolution levels before upload (A-02). */
export async function inspectPictureFile(
  kind: 'wide' | 'phone',
  file: File
): Promise<MediaCheckResult> {
  if (file.size > MAX_MEDIA_BYTES) {
    return { ok: false, error: 'Размер изображения превышает 5 МБ (5 МиБ). Загрузка отменена.' }
  }

  let dimensions: MediaDimensions
  try {
    dimensions = await bannerMediaProbe.readImageDimensions(file)
  } catch {
    return {
      ok: false,
      error: 'Не удалось прочитать файл как изображение. Выберите файл JPG, PNG, WebP или GIF.',
    }
  }

  if (kind === 'phone' && (dimensions.width !== PHONE_IMAGE_WIDTH || dimensions.height !== PHONE_IMAGE_HEIGHT)) {
    return {
      ok: false,
      error: `Размер фото для телефонов должен быть ${PHONE_IMAGE_WIDTH}×${PHONE_IMAGE_HEIGHT}, получено ${dimensions.width}×${dimensions.height}.`,
    }
  }

  return { ok: true, dimensions, warnings: [] }
}
