export const ENDPOINTS = {
  CAMPAIGNS: {
    LIST: '/admin/campaigns',
    GET: (id: number) => `/admin/campaigns/${id}`,
    PUBLISH: (id: number) => `/admin/campaigns/${id}/publish`,
    END: (id: number) => `/admin/campaigns/${id}/end`,
  },
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
  // CRM
  CRM: {
    CUSTOMERS: "/crm/customers",
    STATS: "/crm/customers/stats",
    CUSTOMER: (id: number) => `/crm/customer/${id}`,
  },
  ANALYTICS: {
    DASHBOARD: "/analytics/dashboard",
    REVENUE: "/analytics/revenue",
  },
}
