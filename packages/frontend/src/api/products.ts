import { api } from './client';
import type { Product, Category, PaginatedResponse } from '@/types';

// ═══════════════════════════════════════════════════════════════════
// Products API
// ═══════════════════════════════════════════════════════════════════

interface GetProductsParams {
  categoryId?: string;
  page?: number;
  limit?: number;
  search?: string;
}

export const productsApi = {
  getProducts: (params: GetProductsParams = {}) => {
    const searchParams = new URLSearchParams();
    if (params.categoryId) searchParams.set('categoryId', params.categoryId);
    if (params.page) searchParams.set('page', String(params.page));
    if (params.limit) searchParams.set('limit', String(params.limit));
    if (params.search) searchParams.set('search', params.search);

    const query = searchParams.toString();
    return api.get<PaginatedResponse<Product>>(
      `/products${query ? `?${query}` : ''}`
    );
  },

  getProduct: (productId: string) =>
    api.get<Product>(`/products/${productId}`),

  getCategories: () => api.get<Category[]>('/categories'),

  getCategory: (categoryId: string) =>
    api.get<Category>(`/categories/${categoryId}`),

  getFeaturedProducts: () =>
    api.get<Product[]>('/products/featured'),

  getPopularProducts: () =>
    api.get<Product[]>('/products/popular'),
};
