import { create } from 'zustand';
import { devtools, persist } from 'zustand/middleware';
import WebApp from '@twa-dev/sdk';
import { api } from '@/api/client';
import { STORAGE_KEYS } from '@/utils/constants';

// ═══════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════

export type Language = 'ru' | 'uz';

interface LanguageState {
  language: Language;
  isInitialized: boolean;

  initLanguage: () => Promise<void>;
  setLanguage: (lang: Language) => Promise<void>;
}

// ═══════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════

function getDefaultLanguage(): Language {
  // Try to get from Telegram
  const tgLang = WebApp.initDataUnsafe.user?.language_code;
  return tgLang === 'uz' ? 'uz' : 'ru';
}

// ═══════════════════════════════════════════════════════════════════
// Store
// ═══════════════════════════════════════════════════════════════════

export const useLanguageStore = create<LanguageState>()(
  devtools(
    persist(
      (set, get) => ({
        language: getDefaultLanguage(),
        isInitialized: false,

        initLanguage: async () => {
          if (get().isInitialized) return;

          try {
            // Fetch user profile to get language from backend (source of truth)
            const profile = await api.get<{ language_code: string }>('/users/me');
            const lang = profile.language_code === 'uz' ? 'uz' : 'ru';
            set({ language: lang, isInitialized: true }, false, 'initLanguage');
          } catch {
            // If not authenticated, use default
            set({ isInitialized: true }, false, 'initLanguage/fallback');
          }
        },

        setLanguage: async (lang) => {
          // Always update local state first
          set({ language: lang }, false, 'setLanguage');

          // Try to update backend (will fail in web version without auth, that's ok)
          try {
            await api.patch('/users/me/language', { language_code: lang });
          } catch {
            // Ignore error - language is saved locally via persist middleware
          }
        },
      }),
      {
        name: STORAGE_KEYS.LANGUAGE,
        partialize: (state) => ({ language: state.language }),
      }
    ),
    { name: 'LanguageStore' }
  )
);

// ═══════════════════════════════════════════════════════════════════
// Selectors
// ═══════════════════════════════════════════════════════════════════

export const useLanguage = () => useLanguageStore((state) => state.language);
export const useSetLanguage = () => useLanguageStore((state) => state.setLanguage);
export const useInitLanguage = () => useLanguageStore((state) => state.initLanguage);
