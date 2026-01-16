import { apiClient } from '@/shared/api/client'
import { ENDPOINTS } from '@/shared/api/endpoints'
import type {
  Banner,
  BannerCreateData,
  BannerUpdateData,
  BannerListResponse,
} from '../types/banner.types'
import type { Setting } from '@/shared/types'

export const bannersApi = {
  // Get all banners
  getAll: async (): Promise<BannerListResponse> => {
    const response = await apiClient.get<BannerListResponse>(
      `${ENDPOINTS.BANNERS.LIST}?active_only=false`
    )
    return response.data
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
