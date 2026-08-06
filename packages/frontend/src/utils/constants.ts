// ═══════════════════════════════════════════════════════════════════
// App Constants
// ═══════════════════════════════════════════════════════════════════

export const APP_NAME = 'biotact';

// ═══════════════════════════════════════════════════════════════════
// API Constants
// ═══════════════════════════════════════════════════════════════════

export const API_BASE_URL = import.meta.env.VITE_API_URL || '/api/v1';
export const API_TIMEOUT_MS = 30_000;

// ═══════════════════════════════════════════════════════════════════
// Animation Constants
// ═══════════════════════════════════════════════════════════════════

export const ANIMATION_DURATION_MS = 300;
export const SPRING_CONFIG = {
  type: 'spring' as const,
  stiffness: 300,
  damping: 30,
};

// ═══════════════════════════════════════════════════════════════════
// Pagination Constants
// ═══════════════════════════════════════════════════════════════════

export const DEFAULT_PAGE_SIZE = 20;
export const MAX_PAGE_SIZE = 100;

// ═══════════════════════════════════════════════════════════════════
// Storage Keys
// ═══════════════════════════════════════════════════════════════════

export const STORAGE_KEYS = {
  CART: 'biotact_cart',
  USER: 'biotact_user',
  THEME: 'biotact_theme',
  LANGUAGE: 'biotact_language',
  PENDING_ORDER: 'biotact_pending_order',
} as const;

// ═══════════════════════════════════════════════════════════════════
// Routes
// ═══════════════════════════════════════════════════════════════════

export const ROUTES = {
  HOME: '/',
  CATALOG: '/catalog',
  CATEGORY: '/catalog/:slug',
  PRODUCT: '/product/:productId',
  CART: '/cart',
  CHECKOUT: '/checkout',
  PROFILE: '/profile',
  ACTIVATE: '/activate',
  ORDERS: '/orders',
  ORDER: '/orders/:orderId',
  PAYMENT_SUCCESS: "/payment/success",
  RAMADAN: "/ramadan",
} as const;
