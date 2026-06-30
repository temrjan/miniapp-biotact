// ═══════════════════════════════════════════════════════════════════
// Telegram Types
// ═══════════════════════════════════════════════════════════════════

export interface TelegramUser {
  id: number;
  firstName: string;
  lastName?: string | undefined;
  username?: string | undefined;
  languageCode?: string | undefined;
  isPremium?: boolean | undefined;
  photoUrl?: string | undefined;
}

// ═══════════════════════════════════════════════════════════════════
// Product Types
// ═══════════════════════════════════════════════════════════════════

export interface Product {
  id: string;
  name: string;
  description: string;
  price: number;
  oldPrice?: number | undefined;
  imageUrl: string;
  images?: string[] | undefined;
  categoryId: string;
  inStock: boolean;
  isPurchasable?: boolean | undefined;
  quantity?: number | undefined;
  weight?: string | undefined;
  tags?: string[] | undefined;
}

export interface Category {
  id: string;
  name: string;
  slug: string;
  imageUrl?: string | undefined;
  parentId?: string | undefined;
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
  address?: string | undefined;
  phone?: string | undefined;
  comment?: string | undefined;
  createdAt: string;
  updatedAt: string;
}

// ═══════════════════════════════════════════════════════════════════
// API Types
// ═══════════════════════════════════════════════════════════════════

export interface ApiResponse<T> {
  success: boolean;
  data?: T | undefined;
  error?: string | undefined;
  message?: string | undefined;
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

// ═══════════════════════════════════════════════════════════════════
// Loyalty Types (Biotact Club)
// ═══════════════════════════════════════════════════════════════════

export type LoyaltyTier = 'newbie' | 'biotact_friend';

export interface LoyaltySummary {
  tier: LoyaltyTier;
  balance: number;
  lifetimeActivations: number;
  activationsToFriend: number;
}

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
