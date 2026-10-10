import type { CartItem, Product } from '@/types';

export interface ApiSizeStock {
  requires_size?: boolean;
  available_sizes?: string[];
  is_active?: boolean;
}

export function mapSizeStock(product: ApiSizeStock) {
  return {
    requiresSize: product.requires_size === true,
    availableSizes: product.available_sizes ?? [],
    ...(typeof product.is_active === 'boolean' ? { isActive: product.is_active } : {}),
  };
}

export function isSizedPurchaseBlocked(product: Product): boolean {
  return (
    product.requiresSize === true &&
    (!product.inStock ||
      product.isActive === false ||
      product.isPurchasable === false ||
      !product.availableSizes?.length)
  );
}

export function cartLineKey(item: Pick<CartItem, 'productId' | 'size'>): string {
  return JSON.stringify([item.productId, item.size ?? null]);
}

export function needsSizeChoice(item: CartItem): boolean {
  return (
    item.product.requiresSize === true &&
    (!item.size || !item.product.availableSizes?.includes(item.size))
  );
}
