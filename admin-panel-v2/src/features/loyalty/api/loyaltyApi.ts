import { apiClient } from '@/shared/api'
import type {
  CancelDrawResponse,
  CommitDrawRequest,
  CommitDrawResponse,
  CurrentDrawResponse,
  LoyaltyStats,
  PrintListResponse,
  HoldersFilter,
  RecordWinnersResponse,
  TicketHoldersResponse,
  UpdateDrawResponse,
} from '../types'

export const loyaltyApi = {
  /**
   * Get Biotact Club loyalty monitoring stats (read-only, admin-only).
   * @param days - Window for the QR-activation trend chart (default: 30).
   *                Scopes ONLY `activations_by_day`; all other blocks are all-time or fixed 7/30d.
   */
  getLoyaltyStats: async (days: number = 30): Promise<LoyaltyStats> => {
    const response = await apiClient.get<LoyaltyStats>('/analytics/loyalty', {
      params: { days },
    })
    return response.data
  },

  /** Who holds tickets and on what grounds — the list behind each dashboard number. */
  getTicketHolders: async (
    filter: HoldersFilter,
    page: number,
    size: number
  ): Promise<TicketHoldersResponse> => {
    const response = await apiClient.get<TicketHoldersResponse>('/admin/lottery/holders', {
      params: { filter, page, size },
    })
    return response.data
  },

  /** The draw page's single read: post-filter number to confirm + latest draw of any status. */
  getCurrentDraw: async (): Promise<CurrentDrawResponse> => {
    const response = await apiClient.get<CurrentDrawResponse>('/admin/lottery/current')
    return response.data
  },

  /** The printed list of a draw. 409 on a cancelled one — its list is the export journal. */
  getPrintList: async (drawId: number): Promise<PrintListResponse> => {
    const response = await apiClient.get<PrintListResponse>(`/admin/lottery/${drawId}/print`)
    return response.data
  },

  /** Stamp every eligible clean ticket into a new draw. 409 = live draw / count mismatch / empty. */
  commitDraw: async (payload: CommitDrawRequest): Promise<CommitDrawResponse> => {
    const response = await apiClient.post<CommitDrawResponse>('/admin/lottery/commit', payload)
    return response.data
  },

  /** Record the numbers drawn outside the system. All-or-nothing: any refusal writes nothing. */
  recordWinners: async (drawId: number, winningNumbers: number[]): Promise<RecordWinnersResponse> => {
    const response = await apiClient.post<RecordWinnersResponse>(
      `/admin/lottery/${drawId}/winners`,
      { winning_numbers: winningNumbers }
    )
    return response.data
  },

  /** Cancel a frozen draw: every stamp goes back to its owner. The cancellation must be published. */
  cancelDraw: async (drawId: number): Promise<CancelDrawResponse> => {
    const response = await apiClient.post<CancelDrawResponse>(`/admin/lottery/${drawId}/cancel`)
    return response.data
  },

  /** Set or clear the announced date (outside the published hash, so it is editable). */
  setConductedOn: async (drawId: number, conductedOn: string | null): Promise<UpdateDrawResponse> => {
    const response = await apiClient.patch<UpdateDrawResponse>(`/admin/lottery/${drawId}`, {
      conducted_on: conductedOn,
    })
    return response.data
  },
}
