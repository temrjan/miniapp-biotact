// Banner types based on backend schemas

export interface Banner {
  id: number
  title: string
  title_uz?: string | null
  subtitle?: string | null
  subtitle_uz?: string | null
  image_url?: string | null
  image_mobile_url?: string | null
  video_url?: string | null
  video_mobile_url?: string | null
  gradient?: string | null
  link_url?: string | null
  sort_order: number
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface BannerCreateData {
  title: string
  title_uz?: string | null
  subtitle?: string | null
  subtitle_uz?: string | null
  image_url?: string | null
  image_mobile_url?: string | null
  video_url?: string | null
  video_mobile_url?: string | null
  gradient?: string | null
  link_url?: string | null
  sort_order?: number
  is_active?: boolean
}

export interface BannerUpdateData {
  title?: string
  title_uz?: string | null
  subtitle?: string | null
  subtitle_uz?: string | null
  image_url?: string | null
  image_mobile_url?: string | null
  video_url?: string | null
  video_mobile_url?: string | null
  gradient?: string | null
  link_url?: string | null
  sort_order?: number
  is_active?: boolean
}

export interface BannerListResponse {
  items: Banner[]
  total: number
}
