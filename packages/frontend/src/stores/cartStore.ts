import { useCallback, useEffect, useRef } from 'react';
import { api } from '@/api/client';
import { effectivePrice, mapProductPrice, type ApiProductPrice } from '@/utils/price';
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
                    ? { ...item, product, quantity: item.quantity + quantity }
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
      (total, item) => total + effectivePrice(item.product) * item.quantity,
      0
    )
  );

export const useCartItemQuantity = (productId: string) =>
  useCartStore(
    (state) =>
      state.items.find((item) => item.productId === productId)?.quantity ?? 0
  );

// Refresh only pricing; keep the current cart membership and quantities while requests are in flight.
export function useRefreshCartPrices() {
  const requestVersion = useRef(0);
  // A received checkout quote supersedes every catalog request started before it.
  const cancelRefresh = useCallback(() => { requestVersion.current += 1; }, []);
  const productIds = useCartStore((state) => JSON.stringify(state.items.map((item) => item.productId)));
  useEffect(() => {
    let active = true;
    const version = requestVersion.current;
    const ids: string[] = JSON.parse(productIds);
    if (ids.length === 0) return;
    void Promise.allSettled(ids.map(async (id) => ({
      id,
      price: mapProductPrice(await api.get<ApiProductPrice>(`/products/${id}`)),
    }))).then((results) => {
      if (!active || version !== requestVersion.current) return;
      const prices = new Map(results.flatMap((result) =>
        result.status === 'fulfilled' ? [[result.value.id, result.value.price] as const] : []
      ));
      useCartStore.setState((state) => {
        let changed = false;
        const items = state.items.map((item) => {
          const price = prices.get(item.productId);
          if (!price || (item.product.price === price.price && item.product.oldPrice === price.oldPrice
            && item.product.isSale === price.isSale && item.product.salePrice === price.salePrice)) return item;
          changed = true;
          return { ...item, product: { ...item.product, ...price } };
        });
        return changed ? { items } : state;
      });
    });
    return () => { active = false; };
  }, [productIds]);
  return cancelRefresh;
}
