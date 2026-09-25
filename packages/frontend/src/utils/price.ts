import type { Product } from '@/types';

export interface ApiProductPrice {
  price: number | string;
  old_price: number | string | null;
  is_sale: boolean;
  sale_price: number | string | null;
}

export function mapProductPrice(product: ApiProductPrice) {
  return {
    price: Number(product.price),
    oldPrice: product.old_price == null ? undefined : Number(product.old_price),
    isSale: product.is_sale,
    salePrice: product.sale_price == null ? null : Number(product.sale_price),
  };
}

export function effectivePrice(product: Product): number {
  // Legacy persisted carts have no sale fields. Active sales are validated by the API/DB.
  return product.isSale && product.salePrice != null ? product.salePrice : product.price;
}

export function originalPrice(product: Product): number | undefined {
  return product.isSale ? product.price : product.oldPrice;
}
