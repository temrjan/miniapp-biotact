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
  // What the customer can spend RIGHT NOW = balance minus points pinned by their own unfinished
  // orders (Model-1 reservations). Always drive redemption UI from this, never from `balance`:
  // `balance` is what they own, `available` is what the server would actually admit.
  available: number;
  // When the earliest live reservation lapses; null when nothing is pinned. Word it as
  // "try again after" — reservations are reclaimed by the next attempt, not by a timer.
  reservedUntil: Date | null;
  lifetimeActivations: number;
  activationsToFriend: number;
  isMember: boolean; // true once the user joined the club (a customer_profile row exists)
  // Survey echo — null until answered; lets the profile stop re-asking once saved.
  gender: Gender | null;
  age: number | null;
  city: string | null;
  // 18+ self-confirmation (lottery entry). A boolean, not a nullable answer: "never asked" and
  // "declined" both read as false. Echoed so the checkbox renders as the member left it.
  isAdult: boolean;
}

// How the customer entered the program (sent to POST /loyalty/consent).
export type RegistrationSource = 'qr' | 'direct';

export interface ConsentResult {
  consentRecorded: boolean;
  joined: boolean;
  phoneShared: boolean;
}

// Two legal 200 outcomes of activation — `already_activated` is an idempotent replay
// (points_awarded echoes the ORIGINAL accrual, nothing new is granted), NOT a 409.
export type ActivationStatus = 'activated' | 'already_activated';

export interface ActivationResult {
  status: ActivationStatus;
  tier: LoyaltyTier;
  pointsAwarded: number;
  welcomeBonus: number;
  friendBonus: number;
  balance: number;
  lifetimeActivations: number;
}

// Progressive survey (POST /loyalty/survey) — all fields optional, at least one required.
export type Gender = 'male' | 'female';

export interface SurveyPayload {
  gender?: Gender;
  age?: number;
  city?: string;
  // 18+ confirmation. `false` is a value, not an absence — sending it withdraws a prior
  // confirmation, so omit the key entirely when the member did not touch the checkbox.
  isAdult?: boolean;
}

export interface SurveyResult {
  updatedFields: string[];
  surveyDone: boolean;
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
