import { api } from './client';
import type { Product, Category } from '@/types';
import { parseProduct, parsePage } from './catalogue';

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
  getProducts: async (params: GetProductsParams = {}, language = 'ru') => {
    const searchParams = new URLSearchParams();
    if (params.categoryId) searchParams.set('category_id', params.categoryId);
    if (params.page) searchParams.set('page', String(params.page));
    if (params.limit) searchParams.set('size', String(params.limit));
    if (params.search) searchParams.set('search', params.search);

    const query = searchParams.toString();
    return parsePage(await api.get<unknown>(
      `/products${query ? `?${query}` : ''}`, { headers: { 'X-Language': language } }
    ), parseProduct, params.page ?? 1, params.limit ?? 20);
  },

  getProduct: async (productId: string, language = 'ru') =>
    parseProduct(await api.get<unknown>(`/products/${productId}`, { headers: { 'X-Language': language } })),

  getCategories: () => api.get<Category[]>('/categories'),

  getCategory: (categoryId: string) =>
    api.get<Category>(`/categories/${categoryId}`),

  getFeaturedProducts: () =>
    api.get<Product[]>('/products/featured'),

  getPopularProducts: () =>
    api.get<Product[]>('/products/popular'),
};
