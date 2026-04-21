import { apiClient } from "@/shared/api/client"
import { ENDPOINTS } from "@/shared/api/endpoints"
import type { Product, PaginatedResponse } from "@/shared/types"

export interface ProductsParams {
  page?: number
  size?: number
  category_id?: number | null
  active_only?: boolean
  featured_only?: boolean
  search?: string | null
  sort_by?: string | null
}

export interface ProductCreateData {
  name: string
  name_uz?: string | null | undefined
  slug: string
  description?: string | null | undefined
  description_uz?: string | null | undefined
  price: number
  old_price?: number | null | undefined
  currency?: string | undefined
  stock_quantity?: number | undefined
  is_in_stock?: boolean | undefined
  image_url?: string | null | undefined
  images?: string[] | undefined
  category_id?: number | null | undefined
  mxik?: string | null | undefined
  package_code?: string | null | undefined
  vat?: number | null | undefined
  advantages?: string[] | undefined
  video_url?: string | null | undefined
  additional_info_title?: string | null | undefined
  additional_info_title_uz?: string | null | undefined
  additional_info?: string | null | undefined
  additional_info_uz?: string | null | undefined
  disclaimer?: string | null | undefined
  disclaimer_uz?: string | null | undefined
  sort_order?: number | undefined
  is_active?: boolean | undefined
  is_featured?: boolean | undefined
}

export type ProductUpdateData = Partial<ProductCreateData>

export const productsApi = {
  getProducts: async (params: ProductsParams = {}): Promise<PaginatedResponse<Product>> => {
    const queryParams = new URLSearchParams()

    if (params.page) queryParams.append("page", params.page.toString())
    if (params.size) queryParams.append("size", params.size.toString())
    if (params.category_id) queryParams.append("category_id", params.category_id.toString())
    if (params.active_only !== undefined) queryParams.append("active_only", params.active_only.toString())
    if (params.featured_only) queryParams.append("featured_only", params.featured_only.toString())
    if (params.search) queryParams.append("search", params.search)
    if (params.sort_by) queryParams.append("sort_by", params.sort_by)

    queryParams.append("include_all_translations", "true")

    const response = await apiClient.get<PaginatedResponse<Product>>(
      `${ENDPOINTS.PRODUCTS.LIST}?${queryParams.toString()}`
    )
    return response.data
  },

  getProduct: async (id: number): Promise<Product> => {
    const response = await apiClient.get<Product>(
      `${ENDPOINTS.PRODUCTS.GET(id)}?include_all_translations=true`
    )
    return response.data
  },

  createProduct: async (data: ProductCreateData): Promise<Product> => {
    const response = await apiClient.post<Product>(ENDPOINTS.PRODUCTS.CREATE, data)
    return response.data
  },

  updateProduct: async (id: number, data: ProductUpdateData): Promise<Product> => {
    const response = await apiClient.patch<Product>(ENDPOINTS.PRODUCTS.UPDATE(id), data)
    return response.data
  },

  deleteProduct: async (id: number): Promise<void> => {
    await apiClient.delete(ENDPOINTS.PRODUCTS.DELETE(id))
  },

  reorderProducts: async (items: Array<{ id: number; sort_order: number }>): Promise<void> => {
    await apiClient.post(`${ENDPOINTS.PRODUCTS.LIST}/reorder`, { items })
  },
}
