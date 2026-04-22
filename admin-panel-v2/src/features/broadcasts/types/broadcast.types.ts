// Broadcast types aligned with backend BroadcastResponse / BroadcastCreate schemas

export type BroadcastStatus = 'draft' | 'sending' | 'completed' | 'cancelled' | 'failed'

export interface Broadcast {
  id: number
  title: string
  text: string
  photo_url?: string | null
  video_url?: string | null
  button_text?: string | null
  button_url?: string | null
  status: BroadcastStatus
  total_users: number
  sent_count: number
  failed_count: number
  blocked_count: number
  created_by?: number | null
  created_at: string
  started_at?: string | null
  completed_at?: string | null
}

export interface BroadcastCreateData {
  title: string
  text: string
  photo_url?: string | null
  video_url?: string | null
  button_text?: string | null
  button_url?: string | null
}

export interface BroadcastListResponse {
  items: Broadcast[]
  total: number
  page: number
  size: number
  pages: number
}

export function getStatusLabel(status: BroadcastStatus): string {
  const labels: Record<BroadcastStatus, string> = {
    draft: 'Черновик',
    sending: 'Отправка...',
    completed: 'Завершено',
    cancelled: 'Отменено',
    failed: 'Ошибка',
  }
  return labels[status] ?? status
}

export function getStatusVariant(status: BroadcastStatus): 'default' | 'secondary' | 'destructive' | 'outline' {
  const variants: Record<BroadcastStatus, 'default' | 'secondary' | 'destructive' | 'outline'> = {
    draft: 'secondary',
    sending: 'default',
    completed: 'outline',
    cancelled: 'destructive',
    failed: 'destructive',
  }
  return variants[status] ?? 'secondary'
}
