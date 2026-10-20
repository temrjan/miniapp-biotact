import { create } from 'zustand';
import { devtools, persist } from 'zustand/middleware';
import { api } from '@/api/client';
import { STORAGE_KEYS } from '@/utils/constants';

// ═══════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════

interface Address {
  street: string | null;
  house: string | null;
  apartment: string | null;
  entrance: string | null;
  floor: string | null;
  comment: string | null;
  lat: number | null;
  lng: number | null;
}

interface UserProfile {
  id: number;
  telegram_id: number | null;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  phone: string | null;
  language_code: string;
  is_active: boolean;
  address: Address | null;
}

interface UserState {
  profile: UserProfile | null;
  isLoading: boolean;
  error: string | null;

  fetchProfile: () => Promise<void>;
  updatePhone: (phone: string) => Promise<void>;
  updateAddress: (address: Omit<Address, 'lat' | 'lng'> & { lat?: number; lng?: number }) => Promise<void>;
  clearError: () => void;
  reset: () => void;
}

// ═══════════════════════════════════════════════════════════════════
// Initial State
// ═══════════════════════════════════════════════════════════════════

const initialState = {
  profile: null,
  isLoading: false,
  error: null,
};

// ═══════════════════════════════════════════════════════════════════
// Store
// ═══════════════════════════════════════════════════════════════════

export const useUserStore = create<UserState>()(
  devtools(
    persist(
      (set) => ({
        ...initialState,

        fetchProfile: async () => {
          set({ isLoading: true, error: null }, false, 'fetchProfile/start');
          try {
            const profile = await api.get<UserProfile>('/users/me');
            set({ profile, isLoading: false }, false, 'fetchProfile/success');
          } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to fetch profile';
            set({ error: message, isLoading: false }, false, 'fetchProfile/error');
          }
        },

        updatePhone: async (phone: string) => {
          set({ isLoading: true, error: null }, false, 'updatePhone/start');
          try {
            const profile = await api.patch<UserProfile>('/users/me/phone', { phone });
            set({ profile, isLoading: false }, false, 'updatePhone/success');
          } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to update phone';
            set({ error: message, isLoading: false }, false, 'updatePhone/error');
            throw error;
          }
        },

        updateAddress: async (address) => {
          set({ isLoading: true, error: null }, false, 'updateAddress/start');
          try {
            const profile = await api.patch<UserProfile>('/users/me/address', address);
            set({ profile, isLoading: false }, false, 'updateAddress/success');
          } catch (error) {
            const message = error instanceof Error ? error.message : 'Failed to update address';
            set({ error: message, isLoading: false }, false, 'updateAddress/error');
            throw error;
          }
        },

        clearError: () => set({ error: null }, false, 'clearError'),

        reset: () => set(initialState, false, 'reset'),
      }),
      {
        name: STORAGE_KEYS.USER,
        partialize: (state) => ({ profile: state.profile }),
      }
    ),
    { name: 'UserStore' }
  )
);
