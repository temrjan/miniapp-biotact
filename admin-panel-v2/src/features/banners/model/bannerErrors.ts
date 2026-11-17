import { isAxiosError } from 'axios'
import { isRecord } from '@/shared/api/catalogue'

const FIELD_LABELS: Record<string, string> = {
  title: 'Заголовок (RU)',
  title_uz: 'Заголовок (UZ)',
  subtitle: 'Подзаголовок (RU)',
  subtitle_uz: 'Подзаголовок (UZ)',
  image_url: 'Широкое фото 3:1',
  image_mobile_url: 'Фото для телефонов 3:2',
  video_url: 'Широкий ролик 3:1',
  video_mobile_url: 'Ролик для телефонов 3:2',
  link_url: 'Ссылка',
}

function labelForField(field: string): string {
  return FIELD_LABELS[field] ?? field
}

function readStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value.filter((item): item is string => typeof item === 'string')
}

function formatPydanticErrors(detail: unknown[]): string | null {
  const parts: string[] = []
  for (const entry of detail) {
    if (!isRecord(entry)) continue
    const loc = Array.isArray(entry.loc) ? entry.loc : []
    const fieldName = [...loc]
      .reverse()
      .find((segment): segment is string => typeof segment === 'string' && segment !== 'body')
    const field = fieldName ? labelForField(fieldName) : 'Поле'
    const msg = typeof entry.msg === 'string' ? entry.msg : ''
    const type = typeof entry.type === 'string' ? entry.type : ''

    if (msg.includes('Title must not be blank') || type === 'string_too_short' || type === 'string_type') {
      parts.push(`${field} не может быть пустым`)
    } else if (type === 'string_too_long' || msg.includes('at most')) {
      parts.push(`${field}: превышена допустимая длина`)
    } else if (msg.includes('https URL')) {
      parts.push(`${field}: требуется корректный https-адрес`)
    } else {
      parts.push(`${field}: проверьте введённое значение`)
    }
  }
  const unique = [...new Set(parts)]
  return unique.length > 0 ? `Ошибка проверки данных: ${unique.join('; ')}.` : null
}

function format422Error(detail: unknown): string {
  if (isRecord(detail) && typeof detail.code === 'string') {
    const fields = readStringArray(detail.fields).map(labelForField)
    const fieldText = fields.length > 0 ? fields.join(', ') : 'обязательные поля'
    if (detail.code === 'video_banner_text_required') {
      return `Нельзя сохранить баннер с видео: заполните обязательные поля — ${fieldText}.`
    }
    if (detail.code === 'banner_media_combination') {
      return `Недопустимое сочетание медиафайлов: сначала добавьте ${fieldText}.`
    }
  }

  if (Array.isArray(detail)) {
    const parsed = formatPydanticErrors(detail)
    if (parsed) return parsed
  }

  if (typeof detail === 'string' && detail.trim()) {
    return `Данные отклонены сервером: ${detail}`
  }

  return 'Данные отклонены сервером (ошибка проверки 422).'
}

function format400Error(detail: unknown): string {
  if (typeof detail === 'string') {
    if (detail.startsWith('File type not allowed')) {
      if (detail.includes('.mp4')) {
        return 'Недопустимый формат файла: сервер принимает только ролики .mp4.'
      }
      return 'Недопустимый формат изображения: разрешены JPG, JPEG, PNG, WebP и GIF.'
    }
    if (detail === 'File content is not an MP4 video') {
      return 'Сервер отклонил ролик: содержимое файла не является корректным MP4-видео.'
    }
    if (detail.trim()) {
      return `Сервер отклонил запрос: ${detail}`
    }
  }
  return 'Сервер отклонил запрос (ошибка 400).'
}

/** Translate upload, save, create, and network refusals into plain Russian messages (A-03). */
export function formatBannerError(
  error: unknown,
  fallback = 'Не удалось выполнить действие. Попробуйте ещё раз.'
): string {
  if (isAxiosError<unknown>(error)) {
    if (!error.response) {
      return 'Не удалось связаться с сервером (сетевая ошибка). Изменения не сохранены.'
    }

    const { status, data } = error.response
    const detail = isRecord(data) ? data.detail : undefined

    if (status === 413) {
      return 'Файл слишком большой: сервер отклонил загрузку (превышен лимит 5 МБ или ограничение размера запроса).'
    }
    if (status === 422) {
      return format422Error(detail)
    }
    if (status === 400) {
      return format400Error(detail)
    }
    if (status === 401) {
      return 'Сессия администратора истекла (401). Войдите в систему заново.'
    }
    if (status === 403) {
      return 'Недостаточно прав для этого действия (403).'
    }
    if (status === 404) {
      return 'Баннер не найден на сервере (возможно, он уже удалён). Обновите список.'
    }
    if (status >= 500) {
      return `Ошибка сервера (${status}). Попробуйте позже.`
    }
    return `Сервер отклонил запрос (ошибка ${status}).`
  }

  if (error instanceof Error && error.message.trim()) {
    return error.message
  }

  return fallback
}
