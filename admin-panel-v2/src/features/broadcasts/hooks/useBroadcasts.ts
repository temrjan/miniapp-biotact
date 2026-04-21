import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { broadcastsApi } from '../api/broadcastsApi'
import type {
  Broadcast,
  BroadcastCreateData,
  BroadcastListResponse,
} from '../types/broadcast.types'

export const broadcastKeys = {
  all: ['broadcasts'] as const,
  lists: () => [...broadcastKeys.all, 'list'] as const,
  list: (page: number) => [...broadcastKeys.lists(), page] as const,
  details: () => [...broadcastKeys.all, 'detail'] as const,
  detail: (id: number) => [...broadcastKeys.details(), id] as const,
}

export function useBroadcasts(page = 1) {
  return useQuery<BroadcastListResponse>({
    queryKey: broadcastKeys.list(page),
    queryFn: () => broadcastsApi.getAll(page),
  })
}

export function useBroadcast(id: number | undefined) {
  return useQuery<Broadcast>({
    queryKey: broadcastKeys.detail(id!),
    queryFn: () => broadcastsApi.getById(id!),
    enabled: !!id,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'sending' ? 2000 : false
    },
  })
}

export function useCreateBroadcast() {
  const queryClient = useQueryClient()

  return useMutation<Broadcast, Error, BroadcastCreateData>({
    mutationFn: broadcastsApi.create,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: broadcastKeys.lists() })
    },
  })
}

export function useSendBroadcast() {
  const queryClient = useQueryClient()

  return useMutation<Broadcast, Error, number>({
    mutationFn: broadcastsApi.send,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: broadcastKeys.lists() })
      queryClient.invalidateQueries({ queryKey: broadcastKeys.detail(data.id) })
    },
  })
}

export function useCancelBroadcast() {
  const queryClient = useQueryClient()

  return useMutation<Broadcast, Error, number>({
    mutationFn: broadcastsApi.cancel,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: broadcastKeys.lists() })
      queryClient.invalidateQueries({ queryKey: broadcastKeys.detail(data.id) })
    },
  })
}
