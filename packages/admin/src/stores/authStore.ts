import { create } from 'zustand';
import { persist } from 'zustand/middleware';

interface AdminUser {
  id: number;
  email: string;
  first_name: string | null;
  last_name: string | null;
  is_verified: boolean;
  is_active: boolean;
  created_at: string;
}

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  user: AdminUser | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (accessToken: string, refreshToken: string, user: AdminUser) => void;
  logout: () => void;
  setLoading: (loading: boolean) => void;
  setUser: (user: AdminUser) => void;
  setTokens: (accessToken: string, refreshToken: string) => void;
  refreshAccessToken: () => Promise<boolean>;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      accessToken: null,
      refreshToken: null,
      user: null,
      isAuthenticated: false,
      isLoading: false,

      login: (accessToken: string, refreshToken: string, user: AdminUser) => {
        set({
          accessToken,
          refreshToken,
          user,
          isAuthenticated: true,
          isLoading: false,
        });
      },

      logout: () => {
        set({
          accessToken: null,
          refreshToken: null,
          user: null,
          isAuthenticated: false,
          isLoading: false,
        });
      },

      setLoading: (loading: boolean) => {
        set({ isLoading: loading });
      },

      setUser: (user: AdminUser) => {
        set({ user });
      },

      setTokens: (accessToken: string, refreshToken: string) => {
        set({ accessToken, refreshToken });
      },

      refreshAccessToken: async () => {
        const { refreshToken, logout } = get();
        if (!refreshToken) {
          logout();
          return false;
        }

        try {
          const response = await fetch('/api/v1/auth/refresh', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh_token: refreshToken }),
          });

          if (!response.ok) {
            logout();
            return false;
          }

          const data = await response.json();
          set({
            accessToken: data.access_token,
            refreshToken: data.refresh_token,
          });
          return true;
        } catch {
          logout();
          return false;
        }
      },
    }),
    {
      name: 'admin-auth',
      partialize: (state) => ({
        accessToken: state.accessToken,
        refreshToken: state.refreshToken,
        user: state.user,
        isAuthenticated: state.isAuthenticated,
      }),
    }
  )
);

// API fetch wrapper with auto-refresh
export async function authFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const { accessToken, refreshAccessToken, logout } = useAuthStore.getState();

  const makeRequest = async (token: string | null) => {
    const headers = new Headers(options.headers);
    if (token) {
      headers.set('Authorization', `Bearer ${token}`);
    }
    return fetch(url, { ...options, headers });
  };

  let response = await makeRequest(accessToken);

  // If 401, try to refresh token
  if (response.status === 401) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      const newToken = useAuthStore.getState().accessToken;
      response = await makeRequest(newToken);
    } else {
      logout();
    }
  }

  return response;
}
