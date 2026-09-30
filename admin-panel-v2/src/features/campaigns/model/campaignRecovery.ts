import { z } from 'zod'
import { positiveId, timestamp } from './campaignContract'
import type { CampaignErrorCode, CampaignPage, CampaignRecord, WriteKind } from '../types/campaign.types'

export interface Attempt {
  schema: 1
  actorId: number
  attemptId: string
  kind: WriteKind
  campaignId: number | null
  baseVersion: string | null
  sentAt: string
  createName: string | null
}
const attemptSchema = z.strictObject({
  schema: z.literal(1), actorId: positiveId, attemptId: z.string().uuid(),
  kind: z.enum(['create', 'patch', 'publish', 'end']), campaignId: positiveId.nullable(),
  baseVersion: timestamp.nullable(), sentAt: timestamp,
  createName: z.string().refine(v => [...v].length <= 200).nullable(),
}).refine(a => a.kind === 'create' ? a.campaignId === null && a.baseVersion === null && a.createName !== null :
  a.campaignId !== null && a.baseVersion !== null && a.createName === null)

export type StoredAttempt = Attempt | 'corrupt' | null
export type RecoveryStatus = 'idle' | 'pending' | 'definiteError' | 'conflict' | 'unknown' | 'checking' | 'awaitingDecision' | 'accessDenied'
export interface RecoveryState {
  status: RecoveryStatus
  attempt: StoredAttempt
  message: string | null
  record: CampaignRecord | null
  page: CampaignPage | null
  conflict: boolean
  errorCode: CampaignErrorCode | null
}
export function idleState(): RecoveryState {
  return { status: 'idle', attempt: null, message: null, record: null, page: null, conflict: false, errorCode: null }
}
export const attemptKey = (actorId: number) => `biotact-campaign-attempt-v1:${actorId}`

export function readAttempt(storage: Storage, actorId: number): StoredAttempt {
  const raw = storage.getItem(attemptKey(actorId))
  if (raw === null) return null
  if (new TextEncoder().encode(raw).length > 2048) return 'corrupt'
  try {
    const parsed = attemptSchema.safeParse(JSON.parse(raw) as unknown)
    return parsed.success && parsed.data.actorId === actorId ? parsed.data : 'corrupt'
  } catch { return 'corrupt' }
}

export function storeAttempt(storage: Storage, attempt: Attempt): void {
  if (readAttempt(storage, attempt.actorId) !== null) throw new Error('Unresolved campaign attempt')
  const raw = JSON.stringify(attemptSchema.parse(attempt))
  if (new TextEncoder().encode(raw).length > 2048) throw new Error('Campaign attempt too large')
  storage.setItem(attemptKey(attempt.actorId), raw)
}

export function clearAttempt(storage: Storage, actorId: number, attempt: StoredAttempt): boolean {
  const current = readAttempt(storage, actorId)
  if (attempt === 'corrupt' ? current !== 'corrupt' : !attempt || !current || current === 'corrupt' || current.attemptId !== attempt.attemptId) return false
  storage.removeItem(attemptKey(actorId))
  return true
}

export function writesBlocked(state: RecoveryState): boolean {
  return state.status !== 'idle' && state.status !== 'definiteError'
}
