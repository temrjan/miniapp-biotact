export type UserRole = 'user' | 'admin'

export interface User {
  id: number
  telegram_id: number | null
  username: string | null
  first_name: string | null
  last_name: string | null
  phone_number: string | null
  is_admin: boolean
  is_active: boolean
  created_at: string
  updated_at: string
  orders_count?: number
  total_spent?: string
}

export interface UsersParams {
  page?: number | undefined
  size?: number | undefined
  search?: string | undefined
  role?: UserRole | undefined
  is_active?: boolean | undefined
}

export interface PaginatedUsers {
  items: User[]
  total: number
  page: number
  size: number
  pages: number
}

export interface UserRoleUpdateData {
  is_admin: boolean
}

export const USER_ROLE_LABELS: Record<UserRole, string> = {
  user: 'Пользователь',
  admin: 'Администратор',
}
