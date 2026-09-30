import type { CampaignFields, CampaignRecord, DraftInput, EditInput } from '../types/campaign.types'
import { compareInstants, fromTashkentInput, toTashkentInput } from './campaignDates'

export interface CampaignValues {
  name: string
  product_id: string
  image_url: string
  description: string
  terms: string
  start_at: string
  end_at: string
}
export class FormValidationError extends Error {
  readonly fields: (keyof CampaignValues)[]
  constructor(message: string, fields: (keyof CampaignValues)[]) { super(message); this.fields = fields }
}
export const EMPTY_VALUES: CampaignValues = {
  name: '', product_id: '', image_url: '', description: '', terms: '', start_at: '', end_at: '',
}

export function recordValues(record: CampaignRecord): CampaignValues {
  return { name: record.name, product_id: record.product_id?.toString() ?? '',
    image_url: record.image_url ?? '', description: record.description ?? '', terms: record.terms ?? '',
    start_at: toTashkentInput(record.start_at), end_at: toTashkentInput(record.end_at) }
}

export function validImageUrl(value: string): boolean {
  try {
    const url = new URL(value)
    const rawPath = value.replace(/^https:\/\/[^/]+/, '').split(/[?#]/)[0] ?? ''
    const path = decodeURIComponent(rawPath)
    return url.protocol === 'https:' && !url.username && !url.password && !url.search && !url.hash &&
      !value.includes('?') && !value.includes('#') && path.startsWith('/uploads/') && path.length > 9 &&
      !path.includes('\\') && !path.split('/').some(part => part === '.' || part === '..') &&
      !/%(?:2e|2f|5c)/i.test(path)
  } catch { return false }
}

export function isDirty(values: CampaignValues, saved: CampaignRecord): boolean {
  const baseline = recordValues(saved)
  return (Object.keys(values) as (keyof CampaignValues)[]).some(key => values[key] !== baseline[key])
}

export function formInput(values: CampaignValues, saved?: CampaignRecord): DraftInput | EditInput | null {
  if (!values.name.trim() || [...values.name].length > 200) throw new FormValidationError('Название: от 1 до 200 символов.', ['name'])
  const product = values.product_id ? Number(values.product_id) : null
  if (product !== null && (!/^\d+$/.test(values.product_id) || !Number.isSafeInteger(product) || product <= 0)) {
    throw new FormValidationError('Товар: нужен положительный целый ID.', ['product_id'])
  }
  if (values.image_url && ([...values.image_url].length > 500 || !validImageUrl(values.image_url))) {
    throw new FormValidationError('Используйте HTTPS изображение из загрузок магазина.', ['image_url'])
  }
  const baseline = saved ? recordValues(saved) : null
  const fields: CampaignFields = {
    name: values.name, product_id: product, image_url: values.image_url || null,
    description: values.description || null, terms: values.terms || null,
    start_at: saved && values.start_at === baseline?.start_at ? saved.start_at : fromTashkentInput(values.start_at),
    end_at: saved && values.end_at === baseline?.end_at ? saved.end_at : fromTashkentInput(values.end_at),
  }
  if (fields.start_at && fields.end_at && compareInstants(fields.start_at, fields.end_at) >= 0) {
    throw new FormValidationError('Начало периода должно быть раньше окончания.', ['start_at', 'end_at'])
  }
  if (!saved || !baseline) return fields
  const patch: EditInput = { expected_updated_at: saved.updated_at }
  for (const key of Object.keys(fields) as (keyof CampaignFields)[]) {
    if (values[key] !== baseline[key]) Object.assign(patch, { [key]: fields[key] })
  }
  return Object.keys(patch).length === 1 ? null : patch
}
