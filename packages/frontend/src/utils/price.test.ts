import { describe, expect, it } from 'vitest';
import { effectivePrice, mapProductPrice, originalPrice } from './price';
import type { Product } from '@/types';

const product: Product = {
  id: '1',
  name: 'Product',
  description: '',
  price: 10000,
  oldPrice: 12000,
  imageUrl: '',
  categoryId: '1',
  inStock: true,
};

describe('product price', () => {
  it('should use the sale price and strike the regular price when sale is active', () => {
    const sale = { ...product, isSale: true, salePrice: 8000.5 };
    expect(effectivePrice(sale)).toBe(8000.5);
    expect(originalPrice(sale)).toBe(10000);
  });
  it('should retain the regular and old prices when the sale price is dormant', () => {
    const dormant = { ...product, isSale: false, salePrice: 8000.5 };
    expect(effectivePrice(dormant)).toBe(10000);
    expect(originalPrice(dormant)).toBe(12000);
  });
  it('should retain prices when reading a legacy persisted product', () => {
    expect(effectivePrice(product)).toBe(10000);
    expect(originalPrice(product)).toBe(12000);
  });
  it('should convert decimal API strings when mapping sale prices', () => {
    expect(
      mapProductPrice({
        price: '10000.25',
        old_price: '12000.50',
        is_sale: true,
        sale_price: '8000.50',
      })
    ).toEqual({ price: 10000.25, oldPrice: 12000.5, isSale: true, salePrice: 8000.5 });
    expect(
      mapProductPrice({ price: 10000, old_price: null, is_sale: false, sale_price: null })
    ).toEqual({ price: 10000, oldPrice: undefined, isSale: false, salePrice: null });
  });
});
