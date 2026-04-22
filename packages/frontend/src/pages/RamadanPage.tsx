import { useEffect, useState, useCallback } from 'react';
import { motion } from 'framer-motion';
import { ShoppingCart, Percent, Truck, Clock } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Loading } from '@components/ui/Loading';
import { api } from '@/api/client';
import { useCartStore } from '@/stores/cartStore';
import { useTranslation } from '@/i18n';
import { useLanguage } from '@/stores/languageStore';
import type { Product } from '@/types';

// ═══════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════

interface ApiProduct {
  id: number;
  name: string;
  price: number;
  old_price: number | null;
  image_url: string | null;
  description: string | null;
  category_id: number;
  stock_quantity: number;
  is_in_stock: boolean;
  is_purchasable: boolean;
}

interface ApiCategory {
  id: number;
  name: string;
  slug: string;
  image_url: string | null;
}

// ═══════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════

function mapApiProductToProduct(p: ApiProduct): Product {
  return {
    id: String(p.id),
    name: p.name,
    description: p.description || '',
    price: p.price,
    oldPrice: p.old_price || undefined,
    imageUrl: p.image_url || '',
    categoryId: String(p.category_id),
    inStock: p.is_in_stock,
    isPurchasable: p.is_purchasable,
  };
}

// ═══════════════════════════════════════════════════════════════════
// Sub-components
// ═══════════════════════════════════════════════════════════════════

function RamadanBanner({ t }: { t: (key: string) => string }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="relative -mx-4 -mt-2 overflow-hidden"
    >
      <div className="relative bg-[#0d6b58]">
        <img
          src="/images/ramadan/baner-ramadan.png?v=2"
          alt={t('ramadan.title')}
          className="w-full h-auto block"
        />
        <div className="absolute inset-0 bg-gradient-to-t from-black/60 via-black/20 to-transparent" />
        <div className="absolute bottom-0 left-0 right-0 p-4 pb-5">
          <h1 className="text-white text-2xl font-bold mb-1 drop-shadow-lg">
            {t('ramadan.title')}
          </h1>
          <p className="text-white/80 text-sm drop-shadow-md">
            {t('ramadan.subtitle')}
          </p>
        </div>
      </div>
    </motion.div>
  );
}

function PromoStrip({ t }: { t: (key: string) => string }) {
  const items = [
    {
      icon: <Percent size={20} className="text-red-500" />,
      title: t('ramadan.discount'),
      label: t('ramadan.discountLabel'),
    },
    {
      icon: <Truck size={20} className="text-green-500" />,
      title: t('ramadan.freeDelivery'),
      label: t('ramadan.freeDeliveryLabel'),
    },
    {
      icon: <Clock size={20} className="text-amber-500" />,
      title: t('ramadan.limitedOffer'),
      label: t('ramadan.limitedOfferLabel'),
    },
  ];

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.1 }}
      className="grid grid-cols-3 gap-2"
    >
      {items.map((item, i) => (
        <div
          key={i}
          className="neumorphic-flat p-3 flex flex-col items-center text-center gap-1.5"
        >
          {item.icon}
          <span className="text-xs font-bold text-[var(--text-primary)] leading-tight">
            {item.title}
          </span>
          <span className="text-[10px] text-[var(--text-muted)] leading-tight">
            {item.label}
          </span>
        </div>
      ))}
    </motion.div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Festive Product Card
// ═══════════════════════════════════════════════════════════════════

interface RamadanProductCardProps {
  product: Product;
  index: number;
  onAddToCart: (product: Product) => void;
  onNavigate: (id: string) => void;
  formatPrice: (price: number) => string;
  buttonText: string;
}

function RamadanProductCard({
  product,
  index,
  onAddToCart,
  onNavigate,
  formatPrice,
  buttonText,
}: RamadanProductCardProps) {
  const handleCardClick = useCallback(() => {
    onNavigate(product.id);
  }, [onNavigate, product.id]);

  const handleAddToCart = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onAddToCart(product);
    },
    [onAddToCart, product]
  );

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.15 + index * 0.03 }}
    >
      {/* Gold gradient border wrapper */}
      <div className="relative rounded-2xl p-[1.5px]"
        style={{
          background: 'linear-gradient(135deg, #D4A843 0%, #F0D78C 25%, #D4A843 50%, #F0D78C 75%, #D4A843 100%)',
        }}
      >
        <div className="bg-[var(--bg-surface)] rounded-2xl overflow-hidden h-full flex flex-col relative">
          {/* Image area (clickable) */}
          <div className="cursor-pointer flex-1" onClick={handleCardClick}>
            <div className="relative aspect-square bg-[var(--bg-tertiary)]">
              {product.imageUrl ? (
                <img
                  src={product.imageUrl}
                  alt={product.name}
                  className="w-full h-full object-cover"
                  loading="lazy"
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center text-3xl">
                  📦
                </div>
              )}
              {/* Gold shimmer overlay at bottom of image */}
              <div className="absolute bottom-0 left-0 right-0 h-8 bg-gradient-to-t from-[#D4A843]/10 to-transparent pointer-events-none" />
            </div>

            {/* Price and name */}
            <div className="p-2.5 pb-2">
              <div className="text-center mb-1">
                {product.oldPrice && (
                  <div className="text-[var(--text-muted)] text-[11px] line-through leading-tight">
                    {formatPrice(product.oldPrice)}
                  </div>
                )}
                <div className="text-[var(--primary)] font-bold text-sm">
                  {formatPrice(product.price)}
                </div>
              </div>
              <h3 className="text-xs text-[var(--text-secondary)] text-center line-clamp-2 h-8 leading-4">
                {product.name}
              </h3>
            </div>
          </div>

          {/* Gold separator */}
          <div className="h-[1px] mx-3 bg-gradient-to-r from-transparent via-[#D4A843]/40 to-transparent" />

          {/* Add to cart button with gold tint */}
          {product.isPurchasable !== false && (
            <div
              onClick={handleAddToCart}
              className="w-full py-2.5 text-[#B8860B] text-xs font-semibold flex items-center justify-center gap-1.5 cursor-pointer active:opacity-70 transition-opacity bg-gradient-to-r from-[#D4A843]/5 via-[#D4A843]/10 to-[#D4A843]/5"
            >
              <ShoppingCart size={14} />
              {buttonText}
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Main Component
// ═══════════════════════════════════════════════════════════════════

export function RamadanPage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const language = useLanguage();
  const addToCart = useCartStore((state) => state.addItem);

  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function fetchRamadanProducts() {
      try {
        const catsRes = await api.get<{ items: ApiCategory[] }>('/categories');
        const ramadanCat = (catsRes.items || []).find(
          (c) => c.slug === 'ramadan-set'
        );

        if (ramadanCat) {
          const prodsRes = await api.get<{ items: ApiProduct[] }>(
            `/products?category_id=${ramadanCat.id}&size=50`
          );
          setProducts((prodsRes.items || []).map(mapApiProductToProduct));
        }
      } catch (error) {
        console.error('Failed to fetch ramadan products:', error);
      } finally {
        setLoading(false);
      }
    }

    fetchRamadanProducts();
  }, [language]);

  const formatPrice = useCallback(
    (price: number) => {
      return (
        Math.floor(price).toLocaleString('ru-RU').replace(/,/g, ' ') +
        ' ' +
        t('currency.sum')
      );
    },
    [t]
  );

  const handleNavigateToProduct = useCallback(
    (productId: string) => {
      navigate(`/product/${productId}`);
    },
    [navigate]
  );

  const addToCartText = t('product.addToCart');

  if (loading) {
    return <Loading />;
  }

  return (
    <div className="p-3 pt-2 space-y-4 max-w-4xl mx-auto pb-8">
      <RamadanBanner t={t} />
      <PromoStrip t={t} />

      {products.length > 0 ? (
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
          {products.map((product, index) => (
            <RamadanProductCard
              key={product.id}
              product={product}
              index={index}
              onAddToCart={addToCart}
              onNavigate={handleNavigateToProduct}
              formatPrice={formatPrice}
              buttonText={addToCartText}
            />
          ))}
        </div>
      ) : (
        <div className="text-center py-12 text-[var(--text-muted)]">
          {t('ramadan.empty')}
        </div>
      )}

      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.4 }}
        className="neumorphic-flat p-4 text-center"
      >
        <p className="text-sm text-[var(--text-secondary)]">
          {t('ramadan.promoNote')}
        </p>
      </motion.div>
    </div>
  );
}
