import { create } from 'zustand';
import { devtools } from 'zustand/middleware';
import WebApp from '@twa-dev/sdk';
import type { TelegramUser, ColorScheme } from '@/types';

// ═══════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════

interface AppState {
  user: TelegramUser | null;
  isLoading: boolean;
  colorScheme: ColorScheme;
  isInitialized: boolean;
  
  initApp: () => void;
  setLoading: (loading: boolean) => void;
  setColorScheme: (scheme: ColorScheme) => void;
}

// ═══════════════════════════════════════════════════════════════════
// Helper
// ═══════════════════════════════════════════════════════════════════

function getTelegramUser(): TelegramUser | null {
  const user = WebApp.initDataUnsafe.user;
  
  if (!user) {
    return null;
  }
  
  return {
    id: user.id,
    firstName: user.first_name,
    lastName: user.last_name,
    username: user.username,
    languageCode: user.language_code,
    isPremium: user.is_premium,
    photoUrl: user.photo_url,
  };
}

// ═══════════════════════════════════════════════════════════════════
// Store
// ═══════════════════════════════════════════════════════════════════

export const useAppStore = create<AppState>()(
  devtools(
    (set) => ({
      user: null,
      isLoading: true,
      colorScheme: WebApp.colorScheme,
      isInitialized: false,
      
      initApp: () => {
        const user = getTelegramUser();
        set(
          { user, isLoading: false, isInitialized: true },
          false,
          'initApp'
        );
      },
      
      setLoading: (isLoading) => set({ isLoading }, false, 'setLoading'),
      
      setColorScheme: (colorScheme) => set({ colorScheme }, false, 'setColorScheme'),
    }),
    { name: 'AppStore' }
  )
);

// ═══════════════════════════════════════════════════════════════════
// Selectors
// ═══════════════════════════════════════════════════════════════════

export const useUser = () => useAppStore((state) => state.user);
export const useIsLoading = () => useAppStore((state) => state.isLoading);
export const useAppColorScheme = () => useAppStore((state) => state.colorScheme);
export const useIsInitialized = () => useAppStore((state) => state.isInitialized);
