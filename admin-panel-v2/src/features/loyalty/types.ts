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
