import { isAxiosError } from 'axios'
import { decodeResults } from '../model/campaignResults'
import { decodeSync, parseSyncJSON } from '../model/campaignSync'
import { apiClient } from '@/shared/api/client'
import { ENDPOINTS } from '@/shared/api/endpoints'
import { decodeError, decodePage, decodeRecord, ERROR_MESSAGES } from '../model/campaignContract'
import type { CampaignErrorCode, CampaignPage, CampaignRecord, CampaignState, DraftInput, EditInput } from '../types/campaign.types'

export class CampaignApiError extends Error {
  readonly code: CampaignErrorCode | null
  readonly unknownResult: boolean
  constructor(code: CampaignErrorCode | null, write: boolean) {
    const unknownResult = write && (code === null || code === 'campaign_result_unknown' || code === 'service_unavailable')
    super(unknownResult ? ERROR_MESSAGES.campaign_result_unknown : code ? ERROR_MESSAGES[code] : 'Не удалось получить данные акций.')
    this.code = code
    this.unknownResult = unknownResult
  }
}

async function request<T>(write: boolean, send: () => Promise<{ status: number; data: unknown }>, status: number,
  decode: (data: unknown) => T): Promise<T> {
  try {
    const response = await send()
    if (response.status !== status) throw new CampaignApiError(null, write)
    return decode(response.data)
  } catch (error: unknown) {
    if (error instanceof CampaignApiError) throw error
    const code = isAxiosError<unknown>(error) && error.response ? decodeError(error.response.status, error.response.data) : null
    throw new CampaignApiError(code, write)
  }
}

export const campaignsApi = {
  list: (page = 1, state?: CampaignState, signal?: AbortSignal): Promise<CampaignPage> => request(false,
    () => apiClient.get<unknown>(ENDPOINTS.CAMPAIGNS.LIST, { params: { page, size: 20, ...(state ? { state } : {}) }, ...(signal ? { signal } : {}) }),
    200, data => decodePage(data, page)),
  get: (id: number, signal?: AbortSignal): Promise<CampaignRecord> => request(false,
    () => apiClient.get<unknown>(ENDPOINTS.CAMPAIGNS.GET(id), signal ? { signal } : {}), 200, data => decodeRecord(data, id)),
  sync: (id: number, signal?: AbortSignal) => request(false,
    () => apiClient.get<unknown>(`${ENDPOINTS.CAMPAIGNS.GET(id)}/k1-sync`, {
      ...(signal ? { signal } : {}), transformResponse: [parseSyncJSON],
    }), 200, data => decodeSync(data, id)),
  results: (id: number, signal?: AbortSignal) => request(false,
    async () => {
      const response = await apiClient.get<unknown>(`${ENDPOINTS.CAMPAIGNS.GET(id)}/results`, signal ? { signal } : {})
      const cache: unknown = response.headers['cache-control']
      if (typeof cache !== 'string' || !cache.split(',').some(v => v.trim().toLowerCase() === 'no-store')) throw new CampaignApiError(null, false)
      return response
    }, 200, data => decodeResults(data, id)),
  create: (input: DraftInput): Promise<CampaignRecord> => request(true,
    () => apiClient.post<unknown>(ENDPOINTS.CAMPAIGNS.LIST, input), 201, data => decodeRecord(data, undefined, 'draft')),
  patch: (id: number, input: EditInput): Promise<CampaignRecord> => request(true,
    () => apiClient.patch<unknown>(ENDPOINTS.CAMPAIGNS.GET(id), input), 200, data => decodeRecord(data, id, 'draft', input.expected_updated_at)),
  publish: (id: number, version: string): Promise<CampaignRecord> => request(true,
    () => apiClient.post<unknown>(ENDPOINTS.CAMPAIGNS.PUBLISH(id), { expected_updated_at: version }),
    200, data => decodeRecord(data, id, 'published', version)),
  end: (id: number, version: string): Promise<CampaignRecord> => request(true,
    () => apiClient.post<unknown>(ENDPOINTS.CAMPAIGNS.END(id), { expected_updated_at: version }),
    200, data => decodeRecord(data, id, 'ended', version)),
}
