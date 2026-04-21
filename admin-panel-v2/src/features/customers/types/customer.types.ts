export type ProblemTag = "immunity" | "gut" | "stress" | "skin"

export interface FamilyMember {
  name: string
  relation: string | null
  age: number | null
  problems: string[]
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
}

export interface CustomersParams {
  page?: number | undefined
  size?: number | undefined
  search?: string | undefined
  problem?: ProblemTag | undefined
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
