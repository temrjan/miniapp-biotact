import { effectivePrice, originalPrice, mapProductPrice, type ApiProductPrice } from '@/utils/price';
import { useState, useEffect, useCallback, useRef } from 'react';
import { ShoppingCart, Plus, Minus, X } from 'lucide-react';
import { BottomSheet } from '@/components/ui/BottomSheet';
import { Skeleton } from '@/components/ui/Skeleton';
import { api } from '@/api/client';
import { useCartStore, useCartItemQuantity } from '@/stores/cartStore';
import { useHaptic } from '@/hooks/useTelegram';
import { useTranslation } from '@/i18n';
import type { Product } from '@/types';

// API response type
interface ApiProduct extends ApiProductPrice {
  id: number;
  name: string;
  description: string | null;
  image_url: string | null;
  category_id: number;
  stock_quantity: number;
  is_in_stock: boolean;
  is_purchasable: boolean;
}

function mapApiProduct(api: ApiProduct): Product {
  return {
    id: String(api.id),
    name: api.name,
    description: api.description || '',
    ...mapProductPrice(api),
    imageUrl: api.image_url || '',
    categoryId: String(api.category_id),
    inStock: api.is_in_stock,
    isPurchasable: api.is_purchasable,
  };
}

interface ProductBottomSheetProps {
  productId: string | null;
  isOpen: boolean;
  onClose: () => void;
}

export function ProductBottomSheet({ productId, isOpen, onClose }: ProductBottomSheetProps) {
  const [product, setProduct] = useState<Product | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [localQuantity, setLocalQuantity] = useState(1);
  const lastFetchedId = useRef<string | null>(null);

  const cartQuantity = useCartItemQuantity(productId ?? '');
  const addItem = useCartStore((state) => state.addItem);
  const updateQuantity = useCartStore((state) => state.updateQuantity);
  const haptic = useHaptic();
  const { t } = useTranslation();

  // Format price with currency
  const formatPrice = useCallback(
    (price: number) => {
      return price.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + ' ' + t('currency.sum');
    },
    [t]
  );

  // Fetch product data when productId changes
  useEffect(() => {
    if (!productId || !isOpen) {
      setProduct(null);
      setError(null);
      lastFetchedId.current = null;
      return;
    }

    // Prevent duplicate fetches for same product
    if (lastFetchedId.current === productId) {
      return;
    }

    const fetchProduct = async () => {
      setIsLoading(true);
      setError(null);
      lastFetchedId.current = productId;

      try {
        const data = await api.get<ApiProduct>(`/products/${productId}`);
        const mappedProduct = mapApiProduct(data);
        setProduct(mappedProduct);
      } catch (err) {
        setError('Ошибка загрузки');
        console.error('Failed to fetch product:', err);
        lastFetchedId.current = null;
      } finally {
        setIsLoading(false);
      }
    };

    fetchProduct();
  }, [productId, isOpen]);

  // Set initial quantity when product loads or sheet opens
  useEffect(() => {
    if (isOpen && product) {
      setLocalQuantity(cartQuantity > 0 ? cartQuantity : 1);
    }
  }, [isOpen, product, cartQuantity]);

  const handleIncrement = useCallback(() => {
    haptic.light();
    setLocalQuantity((prev) => prev + 1);
  }, [haptic]);

  const handleDecrement = useCallback(() => {
    if (localQuantity > 1) {
      haptic.light();
      setLocalQuantity((prev) => prev - 1);
    }
  }, [localQuantity, haptic]);

  const handleAddToCart = useCallback(() => {
    if (!product) return;

    haptic.success();

    if (cartQuantity > 0) {
      // Update quantity if already in cart
      updateQuantity(product.id, localQuantity);
    } else {
      // Add new item
      addItem(product, localQuantity);
    }

    onClose();
  }, [product, localQuantity, cartQuantity, addItem, updateQuantity, haptic, onClose]);

  const handleClose = useCallback(() => {
    haptic.light();
    onClose();
  }, [haptic, onClose]);

  return (
    <BottomSheet isOpen={isOpen} onClose={handleClose}>
      {/* Close button */}
      <div className="absolute top-3 right-3 z-10">
        <button
          onClick={handleClose}
          className="p-2 rounded-full bg-[var(--bg-secondary)] hover:bg-[var(--bg-tertiary)] transition-colors"
        >
          <X size={20} className="text-[var(--text-secondary)]" />
        </button>
      </div>

      {isLoading ? (
        <ProductBottomSheetSkeleton />
      ) : error ? (
        <div className="p-6 text-center">
          <p className="text-[var(--text-muted)]">{error}</p>
          <button
            onClick={handleClose}
            className="mt-4 px-4 py-2 rounded-lg bg-[var(--primary)] text-white"
          >
            {t('common.close')}
          </button>
        </div>
      ) : product ? (
        <div className="p-4 space-y-4">
          {/* Product Image */}
          <div className="aspect-square rounded-lg overflow-hidden bg-[var(--bg-tertiary)]">
            <img
              src={product.imageUrl}
              alt={product.name}
              className="w-full h-full object-cover"
            />
          </div>

          {/* Product Info */}
          <div className="space-y-2">
            <h2 className="text-xl font-bold text-[var(--text-primary)]">
              {product.name}
            </h2>

            {product.isSale && <span className="inline-block rounded bg-[var(--primary)] px-2 py-0.5 text-xs text-white">{t('product.sale')}</span>}
            <div className="flex items-baseline gap-3">
              <span className="text-2xl font-bold text-[var(--primary)]">
                {formatPrice(effectivePrice(product))}
              </span>
              {originalPrice(product) != null && (
                <span className="text-lg text-[var(--text-muted)] line-through">
                  {formatPrice(originalPrice(product) ?? 0)}
                </span>
              )}
            </div>
          </div>

          {/* Description - scrollable if long */}
          {product.description && (
            <div className="max-h-32 overflow-y-auto">
              <p className="text-[var(--text-secondary)] text-sm leading-relaxed">
                {product.description}
              </p>
            </div>
          )}

          {/* Quantity Controls */}
          <div className="flex items-center justify-center gap-4 py-2">
            <button
              onClick={handleDecrement}
              disabled={localQuantity <= 1}
              className="neumorphic-sm p-3 rounded-full disabled:opacity-50 disabled:cursor-not-allowed active:scale-95 transition-transform"
            >
              <Minus size={20} className="text-[var(--text-primary)]" />
            </button>

            <span className="text-2xl font-bold w-12 text-center">
              {localQuantity}
            </span>

            <button
              onClick={handleIncrement}
              className="neumorphic-sm p-3 rounded-full active:scale-95 transition-transform"
            >
              <Plus size={20} className="text-[var(--text-primary)]" />
            </button>
          </div>

          {/* Add to Cart Button */}
          {product.isPurchasable !== false && (
            <button
              onClick={handleAddToCart}
              disabled={!product.inStock}
              className="w-full py-4 rounded-xl bg-[var(--primary)] text-white font-semibold flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed active:scale-[0.98] transition-transform"
            >
              <ShoppingCart size={20} />
              <span>
                {cartQuantity > 0
                  ? `${t('product.inCart')}: ${localQuantity} шт`
                  : t('product.addToCart')}
              </span>
              <span className="ml-auto">
                {formatPrice(effectivePrice(product) * localQuantity)}
              </span>
            </button>
          )}
        </div>
      ) : null}
    </BottomSheet>
  );
}

// Loading skeleton
function ProductBottomSheetSkeleton() {
  return (
    <div className="p-4 space-y-4">
      <Skeleton className="aspect-square w-full rounded-lg" />
      <div className="space-y-2">
        <Skeleton className="h-7 w-3/4" />
        <Skeleton className="h-8 w-1/3" />
      </div>
      <div className="space-y-2">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-2/3" />
      </div>
      <div className="flex items-center justify-center gap-4 py-2">
        <Skeleton className="w-12 h-12 rounded-full" />
        <Skeleton className="w-12 h-8" />
        <Skeleton className="w-12 h-12 rounded-full" />
      </div>
      <Skeleton className="h-14 w-full rounded-xl" />
    </div>
  );
}
