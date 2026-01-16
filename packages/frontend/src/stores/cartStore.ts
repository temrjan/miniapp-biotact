import { create } from 'zustand';
import { devtools, persist } from 'zustand/middleware';
import type { CartItem, Product } from '@/types';
import { STORAGE_KEYS } from '@/utils/constants';

// ═══════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════

interface CartState {
  items: CartItem[];
  
  addItem: (product: Product, quantity?: number) => void;
  removeItem: (productId: string) => void;
  updateQuantity: (productId: string, quantity: number) => void;
  clearCart: () => void;
}

// ═══════════════════════════════════════════════════════════════════
// Store
// ═══════════════════════════════════════════════════════════════════

export const useCartStore = create<CartState>()(
  devtools(
    persist(
      (set, get) => ({
        items: [],
        
        addItem: (product, quantity = 1) => {
          const items = get().items;
          const existingItem = items.find((item) => item.productId === product.id);
          
          if (existingItem) {
            set(
              {
                items: items.map((item) =>
                  item.productId === product.id
                    ? { ...item, quantity: item.quantity + quantity }
                    : item
                ),
              },
              false,
              'addItem/increment'
            );
          } else {
            set(
              {
                items: [
                  ...items,
                  { productId: product.id, product, quantity },
                ],
              },
              false,
              'addItem/new'
            );
          }
        },
        
        removeItem: (productId) => {
          set(
            (state) => ({
              items: state.items.filter((item) => item.productId !== productId),
            }),
            false,
            'removeItem'
          );
        },
        
        updateQuantity: (productId, quantity) => {
          if (quantity <= 0) {
            get().removeItem(productId);
            return;
          }
          
          set(
            (state) => ({
              items: state.items.map((item) =>
                item.productId === productId ? { ...item, quantity } : item
              ),
            }),
            false,
            'updateQuantity'
          );
        },
        
        clearCart: () => {
          set({ items: [] }, false, 'clearCart');
        },
      }),
      {
        name: STORAGE_KEYS.CART,
      }
    ),
    { name: 'CartStore' }
  )
);

// ═══════════════════════════════════════════════════════════════════
// Selectors
// ═══════════════════════════════════════════════════════════════════

export const useCartItems = () => useCartStore((state) => state.items);

export const useCartTotalItems = () =>
  useCartStore((state) =>
    state.items.reduce((total, item) => total + item.quantity, 0)
  );

export const useCartTotalAmount = () =>
  useCartStore((state) =>
    state.items.reduce(
      (total, item) => total + item.product.price * item.quantity,
      0
    )
  );

export const useCartItemQuantity = (productId: string) =>
  useCartStore(
    (state) =>
      state.items.find((item) => item.productId === productId)?.quantity ?? 0
  );
