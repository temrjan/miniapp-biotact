import { apiClient } from '@/shared/api/client'
import type {
  Broadcast,
  BroadcastCreateData,
  BroadcastListResponse,
} from '../types/broadcast.types'

const BASE = '/admin/broadcasts'

export const broadcastsApi = {
  getAll: async (page = 1, size = 20): Promise<BroadcastListResponse> => {
    const response = await apiClient.get<BroadcastListResponse>(
      `${BASE}?page=${page}&size=${size}`
    )
    return response.data
  },

  getById: async (id: number): Promise<Broadcast> => {
    const response = await apiClient.get<Broadcast>(`${BASE}/${id}`)
    return response.data
  },

  create: async (data: BroadcastCreateData): Promise<Broadcast> => {
    const response = await apiClient.post<Broadcast>(BASE, data)
    return response.data
  },

  send: async (id: number): Promise<Broadcast> => {
    const response = await apiClient.post<Broadcast>(`${BASE}/${id}/send`)
    return response.data
  },

  cancel: async (id: number): Promise<Broadcast> => {
    const response = await apiClient.post<Broadcast>(`${BASE}/${id}/cancel`)
    return response.data
  },
}
