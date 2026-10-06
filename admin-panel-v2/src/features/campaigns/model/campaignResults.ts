import { z } from 'zod'
import { parseInstant } from './campaignDates'

const quantity = z.string().max(64).regex(/^(0|[1-9][0-9]*)$/).refine(v => v === BigInt(v).toString())
const totals = z.strictObject({ paid_quantity: quantity, returned_quantity: quantity, net_quantity: quantity })
  .refine(v => BigInt(v.returned_quantity) <= BigInt(v.paid_quantity) && BigInt(v.net_quantity) === BigInt(v.paid_quantity) - BigInt(v.returned_quantity))
const rowState = z.enum(['observed', 'incomplete', 'inconsistent', 'unavailable'])
const row = z.strictObject({
  employee_code: z.string().length(36).regex(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/),
  employee_display_name: z.string().refine(v => v.trim().length > 0 && [...v].length <= 161 && !v.includes(String.fromCodePoint(0)) && !/[\uD800-\uDFFF]/u.test(v)),
  state: rowState, totals: totals.nullable(),
}).refine(v => (v.state === 'observed') === (v.totals !== null))
const priority = { observed: 0, incomplete: 1, inconsistent: 2, unavailable: 3 }
export const resultsSchema = z.strictObject({
  schema_version: z.literal(1),
  campaign_id: z.string().max(19).regex(/^[1-9][0-9]*$/).refine(v => v === BigInt(v).toString() && BigInt(v) <= 9223372036854775807n),
  campaign_state: z.enum(['draft', 'published', 'ended']),
  state: z.enum(['preparing', 'observed', 'incomplete', 'inconsistent', 'unavailable']),
  coverage_scope: z.literal('retained-source-v1'),
  observed_at: z.string().length(27).regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$/).refine(v => parseInstant(v) !== null),
  items: z.array(row).max(2000), totals: totals.nullable(),
}).superRefine((v, ctx) => {
  const invalid = () => ctx.addIssue({ code: 'custom', message: 'Invalid campaign results' })
  const codes = v.items.map(r => r.employee_code)
  if (new Set(codes).size !== codes.length || codes.some((code, i) => i > 0 && code < (codes[i - 1] ?? ''))) invalid()
  if (v.state === 'preparing') {
    if (v.items.length !== 0 || v.totals !== null) invalid()
    return
  }
  const state = v.items.reduce<z.infer<typeof rowState>>((a, r) => priority[r.state] > priority[a] ? r.state : a, 'observed')
  if (v.state !== state) invalid()
  if (state !== 'observed') {
    if (v.totals !== null) invalid()
    return
  }
  if (v.totals === null) { invalid(); return }
  for (const key of ['paid_quantity', 'returned_quantity', 'net_quantity'] as const) {
    const sum = v.items.reduce((a, r) => a + (r.totals === null ? 0n : BigInt(r.totals[key])), 0n)
    if (BigInt(v.totals[key]) !== sum) invalid()
  }
})
export type CampaignResults = z.infer<typeof resultsSchema>
export function decodeResults(data: unknown, id: number): CampaignResults {
  const result = resultsSchema.parse(data)
  if (result.campaign_id !== String(id)) throw new Error('Invalid results identity')
  return result
}
