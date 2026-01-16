export const ENDPOINTS = {
  // Auth
  AUTH: {
    LOGIN: "/admin/auth/login",
    LOGOUT: "/admin/auth/logout",
    ME: "/admin/auth/me",
  },
  // Products
  PRODUCTS: {
    LIST: "/products",
    GET: (id: number) => `/products/${id}`,
    CREATE: "/products",
    UPDATE: (id: number) => `/products/${id}`,
    DELETE: (id: number) => `/products/${id}`,
  },
  // Categories
  CATEGORIES: {
    LIST: "/categories",
    GET: (id: number) => `/categories/${id}`,
    CREATE: "/categories",
    UPDATE: (id: number) => `/categories/${id}`,
    DELETE: (id: number) => `/categories/${id}`,
  },
  // Banners
  BANNERS: {
    LIST: "/banners",
    GET: (id: number) => `/banners/${id}`,
    CREATE: "/banners",
    UPDATE: (id: number) => `/banners/${id}`,
    DELETE: (id: number) => `/banners/${id}`,
  },
  // Orders (Admin)
  ORDERS: {
    LIST: "/admin/orders",
    GET: (id: number) => `/admin/orders/${id}`,
    UPDATE_STATUS: (id: number) => `/admin/orders/${id}/status`,
  },
  // Admin endpoints
  ADMIN: {
    USERS: "/admin/users",
    SETTINGS: "/settings",
  },
  // Uploads
  UPLOADS: {
    PRODUCTS: "/uploads/products",
    CATEGORIES: "/uploads/categories",
    BANNERS: "/uploads/banners",
  },
  // Analytics
  ANALYTICS: {
    DASHBOARD: "/analytics/dashboard",
    REVENUE: "/analytics/revenue",
  },
}
