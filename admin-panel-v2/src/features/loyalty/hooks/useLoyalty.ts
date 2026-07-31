import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { loyaltyApi } from '../api/loyaltyApi'
import type { CommitDrawRequest } from '../types'

export const useLoyaltyStats = (days: number = 30) => {
  return useQuery({
    queryKey: ['loyalty', 'stats', days],
    queryFn: () => loyaltyApi.getLoyaltyStats(days),
    staleTime: 1000 * 60 * 5, // 5 minutes
  })
}

const drawKeys = {
  current: ['loyalty', 'draw', 'current'] as const,
  print: (drawId: number) => ['loyalty', 'draw', 'print', drawId] as const,
}

export const useCurrentDraw = () => {
  return useQuery({
    queryKey: drawKeys.current,
    queryFn: loyaltyApi.getCurrentDraw,
  })
}

export const usePrintList = (drawId: number | undefined) => {
  return useQuery({
    queryKey: drawKeys.print(drawId ?? 0),
    queryFn: () => loyaltyApi.getPrintList(drawId as number),
    enabled: drawId !== undefined,
  })
}

/** Every draw mutation invalidates the page's read on SETTLED, not only on success:
 * a 409 (count changed, draw closed by someone else) must refetch so the page shows
 * the server's actual state, not a stale one. */
const useDrawMutation = <TData, TVariables>(
  mutationFn: (variables: TVariables) => Promise<TData>
) => {
  const queryClient = useQueryClient()
  return useMutation<TData, Error, TVariables>({
    mutationFn,
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: drawKeys.current })
      queryClient.invalidateQueries({ queryKey: ['loyalty', 'stats'] })
    },
  })
}

export const useCommitDraw = () => {
  return useDrawMutation((payload: CommitDrawRequest) => loyaltyApi.commitDraw(payload))
}

export const useRecordWinners = (drawId: number) => {
  return useDrawMutation((winningNumbers: number[]) =>
    loyaltyApi.recordWinners(drawId, winningNumbers)
  )
}

export const useCancelDraw = () => {
  return useDrawMutation((drawId: number) => loyaltyApi.cancelDraw(drawId))
}

export const useSetConductedOn = (drawId: number) => {
  return useDrawMutation((conductedOn: string | null) =>
    loyaltyApi.setConductedOn(drawId, conductedOn)
  )
}
