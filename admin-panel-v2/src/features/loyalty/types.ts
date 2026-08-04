// Mirror of backend `LoyaltyStats` (packages/api/src/analytics/schemas.py:146).
// Read-only Biotact Club monitoring snapshot — GET /analytics/loyalty.

export interface ActivationDataPoint {
  date: string // YYYY-MM-DD
  count: number
}

export interface FunnelStep {
  step: string // joined | phone_shared | qr_activated | survey_done
  users: number
}

export interface PointsByReason {
  reason: string
  total: number
}

export interface GenderCount {
  gender: string // 'male' | 'female' | 'unknown'
  count: number
}

export interface AgeBucketCount {
  bucket: string // 18-24 | 25-34 | 35-44 | 45-54 | 55+ | unknown
  count: number
}

export interface CityCount {
  city: string
  count: number
}

export interface Demographics {
  gender: GenderCount[]
  age_buckets: AgeBucketCount[]
  top_cities: CityCount[]
  survey_coverage: number // % of profiles with >=1 survey field filled
}

export interface LoyaltyStats {
  // Block 1 — QR activations (hero)
  qr_generated: number
  qr_activated: number // codes with status='used' (all-time boxes)
  qr_activated_7d: number // customer_event qr_activated in last 7d
  qr_activated_30d: number // customer_event qr_activated in last 30d
  activation_rate: number // qr_activated / qr_generated * 100, 0.0 when generated == 0
  batches_count: number
  activations_by_day: ActivationDataPoint[]

  // Block 2 — Members
  profiles_total: number
  phone_verified: number // count(users WHERE phone_verified_at IS NOT NULL) — GLOBAL, not loyalty-scoped
  friends: number

  // Block 3 — Registration funnel (distinct users per step, counted independently)
  funnel: FunnelStep[]
  consents_ever_granted: number

  // Block 3b — lottery tickets. `eligible_without_survey_ticket` is a detector: it must read 0.
  tickets_total: number
  tickets_clean: number
  eligible_without_survey_ticket: number

  // Block 4 — Points economy
  points_earned: number
  points_spent: number // shown positive
  points_outstanding: number // SUM(amount) = balance still held by everyone
  points_by_reason: PointsByReason[]

  // Block 5 — Demographics
  demographics: Demographics
}

// --- Step 4: draw admin page (mirror of backend step-3/step-4 schemas) ---

export type DrawStatus = 'open' | 'committed' | 'drawn' | 'cancelled'

export interface DrawWinner {
  ticket_number: number
  prize_rank: number
  user_id: number
  first_name: string | null
}

/** Latest draw of ANY status — cancelled included (P-2). */
export interface CurrentDraw {
  draw_id: number
  draw_number: number
  status: DrawStatus
  total_tickets: number
  participant_hash: string | null
  conducted_on: string | null
  prize_label: string | null
  committed_at: string | null
  drawn_at: string | null
  cancelled_at: string | null
  winners: DrawWinner[]
}

/** GET /admin/lottery/current. `eligible_tickets` is the POST-FILTER count —
 * the same number the freeze takes; `clean_tickets` counts every clean ticket. */
export interface CurrentDrawResponse {
  eligible_tickets: number
  clean_tickets: number
  draw: CurrentDraw | null
  /** The last drawn draw, whenever the latest is not itself drawn — winners of the previous
   * draw stay reachable after the next freeze (Гейт-2, БЛОКЕР-1). */
  last_drawn: CurrentDraw | null
}

export interface PrintRow {
  ticket_number: number
  telegram_last4: string
}

/** GET /admin/lottery/{id}/print — 409 on a cancelled draw (its list is the export journal). */
export interface PrintListResponse {
  draw_number: number
  conducted_on: string | null
  total_tickets: number
  rows: PrintRow[]
}

export interface CommitDrawRequest {
  expected_tickets: number
  prize_label?: string
  conducted_on?: string
}

export interface CommitDrawResponse {
  draw_id: number
  draw_number: number
  total_tickets: number
  participant_hash: string
  committed_at: string
  conducted_on: string | null
}

export interface RecordWinnersResponse {
  draw_id: number
  draw_number: number
  status: string
  drawn_at: string
  winners: DrawWinner[]
}

export interface CancelDrawResponse {
  draw_id: number
  draw_number: number
  cancelled_at: string
  released_tickets: number
}

export interface UpdateDrawResponse {
  draw_number: number
  conducted_on: string | null
}

/** One member on the holders screen: how many tickets, on what grounds, and where they are now. */
export interface TicketHolder {
  user_id: number
  name: string
  phone: string | null
  tickets_total: number
  tickets_clean: number
  /** The answer to "for what" — the question the dashboard counters cannot give. */
  by_source: { survey: number; friend: number; activation: number }
  /** One draw only (live freeze, else the last drawn) — never a member's whole history. */
  draw_number: number | null
  numbers: number[]
  played: number
  won: boolean
  eligible_now: boolean
  not_eligible_reason: 'survey' | 'age' | null
  /**
   * Diagnostic column of the `owed` filter. Activation issues every source, so a RECENT date next
   * to a missing survey ticket is a real issuance failure; an absent one is the expected residue of
   * the decision not to backfill. The list is sorted by it, freshest first.
   */
  last_activation_at: string | null
}

export type HoldersFilter = 'all' | 'clean' | 'owed'

export interface TicketHoldersResponse {
  items: TicketHolder[]
  total: number
  page: number
  size: number
}
