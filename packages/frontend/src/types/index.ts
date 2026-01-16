// ═══════════════════════════════════════════════════════════════════
// Telegram Types
// ═══════════════════════════════════════════════════════════════════

export interface TelegramUser {
  id: number;
  firstName: string;
  lastName?: string;
  username?: string;
  languageCode?: string;
  isPremium?: boolean;
  photoUrl?: string;
}

// ═══════════════════════════════════════════════════════════════════
// Product Types
// ═══════════════════════════════════════════════════════════════════

export interface Product {
  id: string;
  name: string;
  description: string;
  price: number;
  oldPrice?: number;
  imageUrl: string;
  images?: string[];
  categoryId: string;
  inStock: boolean;
  quantity?: number;
  weight?: string;
  tags?: string[];
}

export interface Category {
  id: string;
  name: string;
  slug: string;
  imageUrl?: string;
  parentId?: string;
  order: number;
}

// ═══════════════════════════════════════════════════════════════════
// Cart Types
// ═══════════════════════════════════════════════════════════════════

export interface CartItem {
  productId: string;
  product: Product;
  quantity: number;
}

export interface Cart {
  items: CartItem[];
  totalAmount: number;
  totalItems: number;
}

// ═══════════════════════════════════════════════════════════════════
// Order Types
// ═══════════════════════════════════════════════════════════════════

export type OrderStatus = 
  | 'pending'
  | 'confirmed'
  | 'processing'
  | 'shipped'
  | 'delivered'
  | 'cancelled';

export interface Order {
  id: string;
  userId: number;
  items: CartItem[];
  totalAmount: number;
  status: OrderStatus;
  address?: string;
  phone?: string;
  comment?: string;
  createdAt: string;
  updatedAt: string;
}

// ═══════════════════════════════════════════════════════════════════
// API Types
// ═══════════════════════════════════════════════════════════════════

export interface ApiResponse<T> {
  success: boolean;
  data?: T;
  error?: string;
  message?: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
  hasMore: boolean;
}

// ═══════════════════════════════════════════════════════════════════
// UI Types
// ═══════════════════════════════════════════════════════════════════

export type ColorScheme = 'light' | 'dark';
export type Platform = 'ios' | 'android' | 'web' | 'unknown';

export interface ThemeColors {
  primary: string;
  primaryLight: string;
  background: string;
  surface: string;
  text: string;
  textSecondary: string;
  border: string;
  shadowDark: string;
  shadowLight: string;
  success: string;
  error: string;
  warning: string;
}
