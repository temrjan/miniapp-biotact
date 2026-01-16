import { authFetch } from '../stores/authStore';

const API_URL = "/api/v1";

interface ApiResponse<T> {
  items: T[];
  total: number;
  page?: number;
  size?: number;
  pages?: number;
}

export interface Category {
  id: number;
  name: string;
  name_uz: string | null;
  slug: string;
  description: string | null;
  description_uz: string | null;
  image_url: string | null;
  sort_order: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Product {
  id: number;
  name: string;
  name_uz: string | null;
  slug: string;
  description: string | null;
  description_uz: string | null;
  price: number;
  old_price: number | null;
  image_url: string | null;
  category_id: number;
  stock_quantity: number;
  is_active: boolean;
  mxik: string | null;
  package_code: string | null;
  vat: number | null;
  advantages: string[] | null;
  video_url: string | null;
  images: string[] | null;
  additional_info_title: string | null;
  additional_info_title_uz: string | null;
  additional_info: string | null;
  additional_info_uz: string | null;
  disclaimer: string | null;
  disclaimer_uz: string | null;
  created_at: string;
  updated_at: string;
}

export interface Banner {
  id: number;
  title: string;
  title_uz: string | null;
  subtitle: string | null;
  subtitle_uz: string | null;
  image_url: string | null;
  gradient: string | null;
  link_url: string | null;
  sort_order: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface Settings {
  id: number;
  key: string;
  value: string;
  description: string | null;
  updated_at: string;
}

// User types
export interface User {
  id: number;
  telegram_id: number;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  phone: string | null;
  language_code: string;
  is_active: boolean;
  is_admin: boolean;
  created_at: string;
  updated_at: string;
  orders_count: number;
  total_spent: string;
}

export interface UserOrder {
  id: number;
  order_number: string;
  status: string;
  total: string;
  currency: string;
  payment_status: string | null;
  created_at: string;
}

export interface UserDetail extends User {
  address_street: string | null;
  address_house: string | null;
  address_apartment: string | null;
  recent_orders: UserOrder[];
}

export interface UserUpdateData {
  is_active?: boolean;
  is_admin?: boolean;
}

// Order types
export type OrderStatus = 'pending' | 'confirmed' | 'processing' | 'shipped' | 'delivered' | 'cancelled';
export type PaymentStatus = 'unpaid' | 'paid' | 'refunded';

export interface OrderItem {
  id: number;
  product_id: number;
  product_name: string;
  product_price: string;
  quantity: number;
  total: string;
}

export interface Order {
  id: number;
  order_number: string;
  user_id: number;
  status: OrderStatus;
  payment_uuid: string | null;
  payment_status: PaymentStatus | null;
  paid_at: string | null;
  subtotal: string;
  delivery_fee: string;
  total: string;
  currency: string;
  delivery_address: string | null;
  delivery_phone: string | null;
  delivery_name: string | null;
  customer_note: string | null;
  admin_note: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  items: OrderItem[];
}

export interface OrdersParams {
  page?: number;
  size?: number;
  status?: OrderStatus;
  search?: string;
  payment_status?: PaymentStatus;
}

export type CategoryCreate = Omit<Category, "id" | "created_at" | "updated_at">;
export type ProductCreate = Omit<Product, "id" | "created_at" | "updated_at">;
export type BannerCreate = Omit<Banner, "id" | "created_at" | "updated_at">;

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `HTTP error ${response.status}`);
  }

  const text = await response.text();
  if (!text) {
    return undefined as T;
  }
  return JSON.parse(text);
}

async function authFetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await authFetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `HTTP error ${response.status}`);
  }

  const text = await response.text();
  if (!text) {
    return undefined as T;
  }
  return JSON.parse(text);
}

export const api = {
  // Categories
  getCategories: () =>
    fetchJson<ApiResponse<Category>>(`${API_URL}/categories?active_only=false`),

  getCategory: (id: number) =>
    fetchJson<Category>(`${API_URL}/categories/${id}`),

  createCategory: (data: CategoryCreate) =>
    fetchJson<Category>(`${API_URL}/categories`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  updateCategory: (id: number, data: Partial<CategoryCreate>) =>
    fetchJson<Category>(`${API_URL}/categories/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  deleteCategory: (id: number) =>
    fetchJson<void>(`${API_URL}/categories/${id}`, {
      method: "DELETE",
    }),

  // Products
  getProducts: (page = 1, size = 20, categoryId?: number) =>
    fetchJson<ApiResponse<Product>>(`${API_URL}/products?page=${page}&size=${size}&active_only=false&include_all_translations=true${categoryId ? `&category_id=${categoryId}` : ''}`),

  getProduct: (id: number) =>
    fetchJson<Product>(`${API_URL}/products/${id}?include_all_translations=true`),

  createProduct: (data: ProductCreate) =>
    fetchJson<Product>(`${API_URL}/products`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  updateProduct: (id: number, data: Partial<ProductCreate>) =>
    fetchJson<Product>(`${API_URL}/products/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  deleteProduct: (id: number) =>
    fetchJson<void>(`${API_URL}/products/${id}`, {
      method: "DELETE",
    }),

  // Banners
  getBanners: () =>
    fetchJson<ApiResponse<Banner>>(`${API_URL}/banners?active_only=false`),

  getBanner: (id: number) =>
    fetchJson<Banner>(`${API_URL}/banners/${id}`),

  createBanner: (data: BannerCreate) =>
    fetchJson<Banner>(`${API_URL}/banners`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  updateBanner: (id: number, data: Partial<BannerCreate>) =>
    fetchJson<Banner>(`${API_URL}/banners/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  deleteBanner: (id: number) =>
    fetchJson<void>(`${API_URL}/banners/${id}`, {
      method: "DELETE",
    }),

  // Settings
  getSettings: () =>
    fetchJson<Settings[]>(`${API_URL}/settings`),

  getSetting: (key: string) =>
    fetchJson<Settings>(`${API_URL}/settings/${key}`),

  updateSetting: (key: string, value: string) =>
    fetchJson<Settings>(`${API_URL}/settings/${key}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),

  // Admin Users (protected)
  getUsers: (page = 1, size = 20, search?: string, activeOnly = false) => {
    let url = `${API_URL}/admin/users?page=${page}&size=${size}`;
    if (search) url += `&search=${encodeURIComponent(search)}`;
    if (activeOnly) url += `&active_only=true`;
    return authFetchJson<ApiResponse<User>>(url);
  },

  getUser: (id: number) =>
    authFetchJson<UserDetail>(`${API_URL}/admin/users/${id}`),

  updateUser: (id: number, data: UserUpdateData) =>
    authFetchJson<User>(`${API_URL}/admin/users/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  // Admin Orders (protected)
  getOrders: (params: OrdersParams = {}) => {
    const { page = 1, size = 20, status, search, payment_status } = params;
    let url = `${API_URL}/admin/orders?page=${page}&size=${size}`;
    if (status) url += `&status=${status}`;
    if (search) url += `&search=${encodeURIComponent(search)}`;
    if (payment_status) url += `&payment_status=${payment_status}`;
    return authFetchJson<ApiResponse<Order>>(url);
  },

  getOrder: (id: number) =>
    authFetchJson<Order>(`${API_URL}/admin/orders/${id}`),

  updateOrderStatus: (id: number, status: OrderStatus) =>
    authFetchJson<Order>(`${API_URL}/admin/orders/${id}/status`, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    }),
};
