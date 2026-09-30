import { useQuery } from '@tanstack/react-query'
import { skipToken } from '@tanstack/react-query'
import { campaignsApi } from '../api/campaignsApi'
import { useCampaignActions } from './useCampaignActions'
import type { CampaignState } from '../types/campaign.types'

export function useCampaigns(page: number, filter?: CampaignState) {
  const { controller, actorId, state } = useCampaignActions()
  return useQuery({
    queryKey: ['campaigns', actorId, 'list', page, filter ?? 'all'],
    queryFn: ({ signal }) => controller.read(() => campaignsApi.list(page, filter, signal)),
    enabled: actorId !== null && state.status !== 'accessDenied',
    retry: false, networkMode: 'always', refetchOnWindowFocus: false, refetchOnReconnect: false,
  })
}

export function useCampaign(id: number | null) {
  const { controller, actorId, state } = useCampaignActions()
  return useQuery({
    queryKey: ['campaigns', actorId, 'record', id],
    queryFn: id === null ? skipToken : ({ signal }) => controller.read(() => campaignsApi.get(id, signal)),
    enabled: actorId !== null && id !== null && state.status !== 'accessDenied',
    retry: false, networkMode: 'always', refetchOnWindowFocus: false, refetchOnReconnect: false,
  })
}
