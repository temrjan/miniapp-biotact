import { apiClient } from '@/shared/api/client'
import { collection, isRecord } from '@/shared/api/catalogue'
import { ENDPOINTS } from '@/shared/api/endpoints'
import type {
  Banner,
  BannerCreateData,
  BannerUpdateData,
  BannerListResponse,
} from '../types/banner.types'
import type { Setting } from '@/shared/types'

function isBanner(value: unknown): value is Banner {
  return isRecord(value) && typeof value.id === 'number' && Number.isSafeInteger(value.id) && value.id > 0 &&
    typeof value.title === 'string' && typeof value.is_active === 'boolean' &&
    typeof value.sort_order === 'number' && Number.isSafeInteger(value.sort_order) &&
    typeof value.created_at === 'string' && typeof value.updated_at === 'string' &&
    ['title_uz', 'subtitle', 'subtitle_uz', 'image_url', 'gradient', 'link_url'].every(key =>
      value[key] == null || typeof value[key] === 'string')
}

export const bannersApi = {
  // Get all banners
  getAll: async (): Promise<BannerListResponse> => {
    const items: Banner[] = []
    let total: number | undefined
    for (let skip = 0; ; skip += 100) {
      const response = await apiClient.get<unknown>(ENDPOINTS.BANNERS.LIST, { params: { skip, limit: 100 } })
      const data = collection(response.data, skip, 100, total)
      total = data.total
      for (const item of data.items) {
        if (!isBanner(item)) throw new Error('Invalid banner')
        items.push(item)
      }
      if (items.length >= total) return { items, total }
    }
  },

  // Get by ID
  getById: async (id: number): Promise<Banner> => {
    const response = await apiClient.get<Banner>(ENDPOINTS.BANNERS.GET(id))
    return response.data
  },

  // Create
  create: async (data: BannerCreateData): Promise<Banner> => {
    const response = await apiClient.post<Banner>(ENDPOINTS.BANNERS.CREATE, data)
    return response.data
  },

  // Update
  update: async (id: number, data: BannerUpdateData): Promise<Banner> => {
    const response = await apiClient.patch<Banner>(ENDPOINTS.BANNERS.UPDATE(id), data)
    return response.data
  },

  // Delete
  delete: async (id: number): Promise<void> => {
    await apiClient.delete(ENDPOINTS.BANNERS.DELETE(id))
  },

  // Upload banner image
  uploadImage: async (file: File): Promise<string> => {
    const formData = new FormData()
    formData.append('file', file)
    const response = await apiClient.post<{ url: string; filename: string }>(
      ENDPOINTS.UPLOADS.BANNERS,
      formData,
      {
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      }
    )
    return response.data.url
  },

  // Get banner interval setting
  getBannerInterval: async (): Promise<Setting> => {
    const response = await apiClient.get<Setting>('/settings/banner_interval')
    return response.data
  },

  // Update banner interval setting
  updateBannerInterval: async (value: string): Promise<Setting> => {
    const response = await apiClient.put<Setting>('/settings/banner_interval', { value })
    return response.data
  },
}
