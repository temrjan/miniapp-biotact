import { z } from 'zod'
import { compareInstants, parseInstant } from './campaignDates'
import type { CampaignErrorCode, CampaignPage, CampaignRecord, CampaignState } from '../types/campaign.types'

export const positiveId = z.number().int().positive().refine(Number.isSafeInteger)
export const timestamp = z.string().refine(value => parseInstant(value) !== null)
const count = z.number().int().nonnegative().refine(Number.isSafeInteger)
export const campaignRecordSchema = z.strictObject({
  id: positiveId, name: z.string(), product_id: positiveId.nullable(),
  image_url: z.string().nullable(), description: z.string().nullable(), terms: z.string().nullable(),
  state: z.enum(['draft', 'published', 'ended']),
  start_at: timestamp.nullable(), end_at: timestamp.nullable(),
  published_at: timestamp.nullable(), ended_at: timestamp.nullable(), created_at: timestamp, updated_at: timestamp,
})
const pageSchema = z.strictObject({
  items: z.array(campaignRecordSchema), total: count, page: positiveId, size: positiveId, pages: count,
})

export function decodeRecord(data: unknown, id?: number, state?: CampaignState, base?: string): CampaignRecord {
  const record = campaignRecordSchema.parse(data)
  if ((id !== undefined && record.id !== id) || (state !== undefined && record.state !== state) ||
      (base !== undefined && compareInstants(record.updated_at, base) <= 0)) {
    throw new Error('Unconfirmed campaign record')
  }
  return record
}

export function decodePage(data: unknown, page: number): CampaignPage {
  const result = pageSchema.parse(data)
  if (result.page !== page || result.size !== 20 || result.pages !== Math.ceil(result.total / 20)) {
    throw new Error('Invalid campaign page')
  }
  return result
}

export const ERROR_STATUS: Record<CampaignErrorCode, number> = {
  invalid_admin_auth: 401, admin_forbidden: 403, campaign_not_found: 404,
  campaign_changed: 409, invalid_campaign_state: 409, immutable_campaign: 409, campaign_overlap: 409,
  invalid_request: 422, invalid_campaign_dates: 422, incomplete_campaign: 422, invalid_image_url: 422,
  product_not_found: 422, product_inactive: 422, product_not_purchasable: 422, product_out_of_stock: 422,
  shop_unavailable: 503, service_unavailable: 503, campaign_service_unavailable: 503, campaign_result_unknown: 503,
}

const errorSchema = z.strictObject({ detail: z.strictObject({
  code: z.enum(Object.keys(ERROR_STATUS) as [CampaignErrorCode, ...CampaignErrorCode[]]), message: z.string(),
}) })

export function decodeError(status: number, data: unknown): CampaignErrorCode | null {
  const parsed = errorSchema.safeParse(data)
  return parsed.success && ERROR_STATUS[parsed.data.detail.code] === status ? parsed.data.detail.code : null
}

export const ERROR_MESSAGES: Record<CampaignErrorCode, string> = {
  invalid_admin_auth: 'Войдите в административную панель заново.',
  admin_forbidden: 'Доступ к акциям запрещён. Нужна новая проверка полномочий.',
  campaign_not_found: 'Акция не найдена. Вернитесь к списку.',
  campaign_changed: 'Акция изменена. Проверьте актуальную запись.',
  invalid_campaign_state: 'Состояние акции изменилось. Проверьте актуальную запись.',
  immutable_campaign: 'После публикации акция не редактируется.',
  campaign_overlap: 'Период пересекается с опубликованной акцией этого товара.',
  invalid_request: 'Проверьте формат и обязательные поля.',
  invalid_campaign_dates: 'Проверьте начало и окончание периода.',
  incomplete_campaign: 'Для публикации нужны товар, изображение, описание, условия и даты.',
  invalid_image_url: 'Используйте HTTPS изображение из загрузок магазина.',
  product_not_found: 'Товар не найден. Выберите другой товар.', product_inactive: 'Товар отключён.',
  product_not_purchasable: 'Товар недоступен для покупки.', product_out_of_stock: 'Товар отмечен как отсутствующий.',
  shop_unavailable: 'Не удалось проверить товар в магазине.',
  service_unavailable: 'Сервис акций временно недоступен.',
  campaign_service_unavailable: 'Подключение акций недоступно. Обратитесь к ответственному за систему.',
  campaign_result_unknown: 'Результат действия не подтверждён.',
}
