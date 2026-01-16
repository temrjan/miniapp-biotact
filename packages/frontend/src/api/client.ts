import WebApp from '@twa-dev/sdk';

import { useLanguageStore } from '@/stores/languageStore';
import { API_BASE_URL, API_TIMEOUT_MS } from '@/utils/constants';

// ═══════════════════════════════════════════════════════════════════
// API Client Configuration
// ═══════════════════════════════════════════════════════════════════

interface RequestConfig extends RequestInit {
  timeout?: number;
}

class ApiError extends Error {
  status: number;
  data?: unknown;

  constructor(message: string, status: number, data?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.data = data;
  }
}

// ═══════════════════════════════════════════════════════════════════
// Base Fetch with Auth
// ═══════════════════════════════════════════════════════════════════

async function fetchWithAuth<T>(
  endpoint: string,
  config: RequestConfig = {}
): Promise<T> {
  const { timeout = API_TIMEOUT_MS, ...fetchConfig } = config;

  // Get current language from store (Zustand allows getState outside React)
  const language = useLanguageStore.getState().language;

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeout);

  try {
    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
      ...fetchConfig,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        'X-Init-Data': WebApp.initData,
        'Accept-Language': language,
        ...fetchConfig.headers,
      },
    });

    clearTimeout(timeoutId);

    if (!response.ok) {
      const errorData = await response.json().catch(() => null);
      throw new ApiError(
        errorData?.message || `HTTP ${response.status}`,
        response.status,
        errorData
      );
    }

    return response.json();
  } catch (error) {
    clearTimeout(timeoutId);

    if (error instanceof Error && error.name === 'AbortError') {
      throw new ApiError('Request timeout', 408);
    }

    throw error;
  }
}

// ═══════════════════════════════════════════════════════════════════
// HTTP Methods
// ═══════════════════════════════════════════════════════════════════

export const api = {
  get: <T>(endpoint: string, config?: RequestConfig) =>
    fetchWithAuth<T>(endpoint, { ...config, method: 'GET' }),

  post: <T>(endpoint: string, data?: unknown, config?: RequestConfig) =>
    fetchWithAuth<T>(endpoint, {
      ...config,
      method: 'POST',
      body: data ? JSON.stringify(data) : undefined,
    }),

  put: <T>(endpoint: string, data?: unknown, config?: RequestConfig) =>
    fetchWithAuth<T>(endpoint, {
      ...config,
      method: 'PUT',
      body: data ? JSON.stringify(data) : undefined,
    }),

  patch: <T>(endpoint: string, data?: unknown, config?: RequestConfig) =>
    fetchWithAuth<T>(endpoint, {
      ...config,
      method: 'PATCH',
      body: data ? JSON.stringify(data) : undefined,
    }),

  delete: <T>(endpoint: string, config?: RequestConfig) =>
    fetchWithAuth<T>(endpoint, { ...config, method: 'DELETE' }),
};

export { ApiError };
