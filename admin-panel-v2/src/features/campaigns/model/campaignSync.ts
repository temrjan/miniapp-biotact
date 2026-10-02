import { z } from 'zod'
import { parseInstant } from './campaignDates'

const bigint = z.string().regex(/^[1-9][0-9]{0,18}$/).refine(v => /^[1-9][0-9]{0,18}$/.test(v) && BigInt(v) <= 9223372036854775807n)
const time = z.string().regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/).refine(v => parseInstant(v) !== null)
const count = z.number().int().nonnegative().refine(Number.isSafeInteger)
const bit = z.number().int().min(0).max(1)
export const syncSchema = z.strictObject({
  campaign_id: bigint, campaign_state: z.enum(['draft', 'published', 'ended']), sender_enabled: z.boolean(),
  status: z.enum(['not_published', 'disabled', 'pending', 'confirmed', 'expired', 'blocked']),
  desired_revision: bigint.nullable(), confirmed_revision: bigint.nullable(), participant_count: count,
  confirmed_at: time.nullable(), valid_until: time.nullable(), stop_confirmed: z.boolean(), stop_deadline: time.nullable(),
  pending_count: bit, blocked_count: bit, oldest_pending_at: time.nullable(),
  last_error_code: z.enum(['configuration', 'clock', 'transport', 'protocol', 'authentication', 'conflict',
    'payload_limit', 'receiver_disabled', 'invalid_request', 'attempt_limit', 'database', 'storage']).nullable(),
  observed_at: time,
})
export type CampaignSync = z.infer<typeof syncSchema>
export const EXPIRED_TEXT = 'Срок подтверждения истёк. Актуальное состояние магазина не подтверждено.'

// The DTO is flat. Validate JSON syntax first, then check every decoded object key,
// including escaped duplicates, before the platform parser can discard them.
export function parseSyncJSON(raw: unknown): unknown {
  if (typeof raw !== 'string') return raw
  const data: unknown = JSON.parse(raw)
  const keys = new Set<string>()
  for (const match of raw.matchAll(/"((?:[^"\\]|\\.)*)"\s*:/g)) {
    const key: string = JSON.parse(`"${match[1]}"`)
    if (keys.has(key)) throw new Error('Duplicate sync field')
    keys.add(key)
  }
  return data
}
export function decodeSync(data: unknown, id: number): CampaignSync {
  const result = syncSchema.parse(data)
  if (result.campaign_id !== String(id)) throw new Error('Invalid sync identity')
  return result
}

export function remainingMs(deadline: string, observed: string, elapsed: number): number {
  // UTC microseconds are rounded towards earlier expiry, never extending a lease.
  const end = parseInstant(deadline), start = parseInstant(observed)
  if (!end || !start) return 0
  const micros = (end.seconds - start.seconds) * 1000000n +
    BigInt(end.fraction.padEnd(6, '0')) - BigInt(start.fraction.padEnd(6, '0'))
  return Math.max(0, Number(micros / 1000n) - Math.ceil(Math.max(0, elapsed)))
}
