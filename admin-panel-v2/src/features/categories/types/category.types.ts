// Category types based on backend schemas

export interface Category {
  id: number;
  name: string;
  name_uz?: string;
  slug: string;
  description?: string;
  description_uz?: string;
  image_url?: string;
  sort_order: number;
  is_active: boolean;
  is_purchasable: boolean;
  created_at: string;
  updated_at: string;
}

export interface CategoryCreateData {
  name: string;
  name_uz?: string | undefined;
  slug: string;
  description?: string | undefined;
  description_uz?: string | undefined;
  image_url?: string | undefined;
  sort_order?: number | undefined;
  is_active?: boolean | undefined;
  is_purchasable?: boolean | undefined;
}

export interface CategoryUpdateData {
  name?: string | undefined;
  name_uz?: string | undefined;
  slug?: string | undefined;
  description?: string | undefined;
  description_uz?: string | undefined;
  image_url?: string | undefined;
  sort_order?: number | undefined;
  is_active?: boolean | undefined;
  is_purchasable?: boolean | undefined;
}

export interface CategoryListResponse {
  items: Category[];
  total: number;
  page: number;
  size: number;
  pages: number;
}
