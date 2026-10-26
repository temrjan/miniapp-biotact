import { apiClient } from '@/shared/api/client';
import { categoryPage, isRecord } from '@/shared/api/catalogue';
import type {
  Category,
  CategoryCreateData,
  CategoryUpdateData,
  CategoryListResponse,
} from '../types/category.types';

export const categoriesApi = {
  getChoices: async (): Promise<Pick<Category, 'id' | 'name' | 'name_uz'>[]> => {
    const items: Pick<Category, 'id' | 'name' | 'name_uz'>[] = [];
    let total: number | undefined;
    for (let page = 1; ; page++) {
      const response = await apiClient.get<unknown>('/categories', {
        params: { page, size: 100, active_only: false },
      });
      const data = categoryPage(response.data, page, 100, total);
      total = data.total;
      for (const item of data.items) {
        if (!isRecord(item) || typeof item.id !== 'number' || !Number.isSafeInteger(item.id) || item.id < 1 ||
            typeof item.name !== 'string' || (item.name_uz != null && typeof item.name_uz !== 'string'))
          throw new Error('Invalid category choice');
        items.push({ id: item.id, name: item.name, ...(item.name_uz == null ? {} : { name_uz: item.name_uz }) });
      }
      if (page >= data.pages) return items;
    }
  },
  // Get paginated list
  getAll: async (params?: {
    page?: number;
    size?: number;
    active_only?: boolean;
  }): Promise<CategoryListResponse> => {
    const response = await apiClient.get<CategoryListResponse>('/categories', {
      params: params || { page: 1, size: 100, active_only: false },
    });
    return response.data;
  },

  // Get by ID
  getById: async (id: number): Promise<Category> => {
    const response = await apiClient.get<Category>(`/categories/${id}`);
    return response.data;
  },

  // Get by slug
  getBySlug: async (slug: string): Promise<Category> => {
    const response = await apiClient.get<Category>(`/categories/slug/${slug}`);
    return response.data;
  },

  // Create
  create: async (data: CategoryCreateData): Promise<Category> => {
    const response = await apiClient.post<Category>('/categories', data);
    return response.data;
  },

  // Update
  update: async (id: number, data: CategoryUpdateData): Promise<Category> => {
    const response = await apiClient.patch<Category>(`/categories/${id}`, data);
    return response.data;
  },

  // Delete
  delete: async (id: number): Promise<void> => {
    await apiClient.delete(`/categories/${id}`);
  },
};
