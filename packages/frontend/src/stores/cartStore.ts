import { useCallback, useEffect, useRef } from 'react';
import { api } from '@/api/client';
import { effectivePrice, mapProductPrice, type ApiProductPrice } from '@/utils/price';
import { create } from 'zustand';
import { devtools, persist } from 'zustand/middleware';
import type { CartItem, Product } from '@/types';
import { STORAGE_KEYS } from '@/utils/constants';
import { isSizedPurchaseBlocked, mapSizeStock, type ApiSizeStock } from '@/utils/size';

// ═══════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════

interface CartState {
  items: CartItem[];
  
  addItem: (product: Product, quantity?: number, size?: string | null) => void;
  removeItem: (productId: string, size?: string | null) => void;
  updateQuantity: (productId: string, quantity: number, size?: string | null) => void;
  chooseSize: (productId: string, previousSize: string | null, size: string) => void;
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
        
        addItem: (product, quantity = 1, size = null) => {
          if (product.requiresSize && (!size || !product.availableSizes?.includes(size) || isSizedPurchaseBlocked(product))) return;
          if (!product.requiresSize && size !== null) return;
          const items = get().items;
          const existingItem = items.find((item) => item.productId === product.id && (item.size ?? null) === size);
          
          if (existingItem) {
            set(
              {
                items: items.map((item) =>
                  item.productId === product.id && (item.size ?? null) === size
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
                  { productId: product.id, product, quantity, ...(size ? { size } : {}) },
                ],
              },
              false,
              'addItem/new'
            );
          }
        },
        
        removeItem: (productId, size = null) => {
          set(
            (state) => ({
              items: state.items.filter((item) => item.productId !== productId || (item.size ?? null) !== size),
            }),
            false,
            'removeItem'
          );
        },
        
        updateQuantity: (productId, quantity, size = null) => {
          if (quantity <= 0) {
            get().removeItem(productId, size);
            return;
          }
          
          set(
            (state) => ({
              items: state.items.map((item) =>
                item.productId === productId && (item.size ?? null) === size ? { ...item, quantity } : item
              ),
            }),
            false,
            'updateQuantity'
          );
        },

        chooseSize: (productId, previousSize, size) => {
          const item = get().items.find((line) => line.productId === productId && (line.size ?? null) === previousSize);
          if (!item || !item.product.availableSizes?.includes(size) || previousSize === size) return;
          set((state) => {
            const target = state.items.find((line) => line.productId === productId && line.size === size);
            return { items: state.items.flatMap((line) => {
              if (line === item) return target ? [] : [{ ...line, size }];
              return [line === target ? { ...line, quantity: line.quantity + item.quantity } : line];
            }) };
          }, false, 'chooseSize');
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

export const useCartItemQuantity = (productId: string, size: string | null = null) =>
  useCartStore(
    (state) =>
      state.items.find((item) => item.productId === productId && (item.size ?? null) === size)?.quantity ?? 0
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
      data: await api.get<ApiProductPrice & ApiSizeStock & { is_in_stock?: boolean; is_purchasable?: boolean }>(`/products/${id}`),
    }))).then((results) => {
      if (!active || version !== requestVersion.current) return;
      const prices = new Map(results.flatMap((result) =>
        result.status === 'fulfilled' ? [[result.value.id, result.value.data] as const] : []
      ));
      useCartStore.setState((state) => {
        let changed = false;
        const items = state.items.map((item) => {
          const data = prices.get(item.productId);
          if (!data) return item;
          const price = mapProductPrice(data);
          const inventory = mapSizeStock(data);
          const product = { ...item.product, ...price, ...inventory,
            ...(inventory.requiresSize ? { quantity: undefined } : {}),
            inStock: data.is_in_stock ?? item.product.inStock,
            isPurchasable: data.is_purchasable ?? item.product.isPurchasable };
          if (JSON.stringify(item.product) === JSON.stringify(product)) return item;
          changed = true;
          return { ...item, product };
        });
        return changed ? { items } : state;
      });
    });
    return () => { active = false; };
  }, [productIds]);
  return cancelRefresh;
}
