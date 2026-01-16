import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { bannersApi } from '../api/bannersApi'
import type {
  Banner,
  BannerCreateData,
  BannerUpdateData,
  BannerListResponse,
} from '../types/banner.types'
import type { Setting } from '@/shared/types'

// Query keys
export const bannerKeys = {
  all: ['banners'] as const,
  lists: () => [...bannerKeys.all, 'list'] as const,
  list: () => [...bannerKeys.lists()] as const,
  details: () => [...bannerKeys.all, 'detail'] as const,
  detail: (id: number) => [...bannerKeys.details(), id] as const,
  settings: () => [...bannerKeys.all, 'settings'] as const,
  interval: () => [...bannerKeys.settings(), 'interval'] as const,
}

// Get all banners
export function useBanners() {
  return useQuery<BannerListResponse>({
    queryKey: bannerKeys.list(),
    queryFn: bannersApi.getAll,
  })
}

// Get single banner by ID
export function useBanner(id: number | undefined) {
  return useQuery<Banner>({
    queryKey: bannerKeys.detail(id!),
    queryFn: () => bannersApi.getById(id!),
    enabled: !!id,
  })
}

// Create banner
export function useCreateBanner() {
  const queryClient = useQueryClient()

  return useMutation<Banner, Error, BannerCreateData>({
    mutationFn: bannersApi.create,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: bannerKeys.lists() })
    },
  })
}

// Update banner
export function useUpdateBanner() {
  const queryClient = useQueryClient()

  return useMutation<
    Banner,
    Error,
    { id: number; data: BannerUpdateData }
  >({
    mutationFn: ({ id, data }) => bannersApi.update(id, data),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: bannerKeys.lists() })
      queryClient.invalidateQueries({ queryKey: bannerKeys.detail(variables.id) })
    },
  })
}

// Delete banner
export function useDeleteBanner() {
  const queryClient = useQueryClient()

  return useMutation<void, Error, number>({
    mutationFn: bannersApi.delete,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: bannerKeys.lists() })
    },
  })
}

// Upload banner image
export function useUploadBannerImage() {
  return useMutation<string, Error, File>({
    mutationFn: bannersApi.uploadImage,
  })
}

// Get banner interval setting
export function useBannerInterval() {
  return useQuery<Setting>({
    queryKey: bannerKeys.interval(),
    queryFn: bannersApi.getBannerInterval,
  })
}

// Update banner interval setting
export function useUpdateBannerInterval() {
  const queryClient = useQueryClient()

  return useMutation<Setting, Error, string>({
    mutationFn: bannersApi.updateBannerInterval,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: bannerKeys.interval() })
    },
  })
}
