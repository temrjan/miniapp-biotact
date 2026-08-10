export type ProblemTag = "immunity" | "gut" | "stress" | "skin"

export interface FamilyMember {
  name: string
  relation: string | null
  age: number | null
  problems: string[]
}

// Biotact Club slice — present only for club members (null/absent otherwise).
export interface CustomerLoyalty {
  source: "qr" | "direct" | null
  status: "newbie" | "biotact_friend"
  lifetime_activations: number
  gender: "male" | "female" | null
  age: number | null
  city: string | null
  balance: number
}

export interface Customer {
  telegram_id: number
  first_name: string | null
  last_name: string | null
  username: string | null
  language_code: string
  phone: string | null
  problems: string[]
  family: FamilyMember[]
  purchased_products: string[]
  ai_notes: string | null
  created_at: string
  updated_at: string
  // Optional: an older API omits the key entirely (deploy window) — treat as non-member.
  loyalty?: CustomerLoyalty | null
}

// The club filter values the API accepts. Named rather than repeated: the same union is also the
// page's state (widened by an "all" sentinel) and the cast that narrows the Select's plain string
// back to it. A literal written in three places is one edit away from an annotation that lies —
// the cast keeps compiling and silently claims a value it no longer covers.
export type ClubFilter = "member" | "qr" | "no_survey"

export interface CustomersParams {
  page?: number | undefined
  size?: number | undefined
  search?: string | undefined
  problem?: ProblemTag | undefined
  club?: ClubFilter | undefined
}

export interface PaginatedCustomers {
  items: Customer[]
  total: number
  page: number
  size: number
  pages: number
}

export interface CustomersStats {
  total_customers: number
  new_today: number
  with_phone: number
  with_purchases: number
  by_problem: Record<ProblemTag, number>
}

export const PROBLEM_LABELS: Record<ProblemTag, string> = {
  immunity: "Иммунитет",
  gut: "ЖКТ",
  stress: "Стресс",
  skin: "Кожа",
}

export const PROBLEM_COLORS: Record<ProblemTag, string> = {
  immunity: "bg-blue-100 text-blue-800",
  gut: "bg-green-100 text-green-800",
  stress: "bg-purple-100 text-purple-800",
  skin: "bg-orange-100 text-orange-800",
}

// Record<string, string> (not the union) on purpose: an unknown wire value indexes to
// undefined under noUncheckedIndexedAccess and falls back to the raw value at the call site.
export const CLUB_STATUS_LABELS: Record<string, string> = {
  newbie: "Новичок",
  biotact_friend: "Друг",
}

export const GENDER_SHORT: Record<string, string> = {
  male: "муж",
  female: "жен",
}
