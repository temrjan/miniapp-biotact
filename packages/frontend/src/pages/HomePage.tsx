import { isSizedPurchaseBlocked, mapSizeStock } from '@/utils/size';
import { effectivePrice, originalPrice, mapProductPrice } from '@/utils/price';
import { useEffect, useState, useCallback, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronRight, ChevronDown, ShoppingCart } from 'lucide-react';
import { ROUTES } from '@/utils/constants';
import { Link, useNavigate } from 'react-router-dom';
import { Loading } from '@components/ui/Loading';
import { BannerCarousel } from '@components/features/BannerCarousel';
import { loadCategories, loadProducts, loadActiveBanners, type CatalogueCategory as ApiCategory, type CatalogueProduct as ApiProduct, type CatalogueBanner as ApiBanner } from '@/api/catalogue';
import { api } from '@/api/client';
import { useCartStore } from '@/stores/cartStore';
import { useTranslation } from '@/i18n';
import { useLanguage } from '@/stores/languageStore';
import type { Product } from '@/types';

// ═══════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════




interface ProductCardProps {
  product: Product;
  index: number;
  onAddToCart: (product: Product) => void;
  onNavigate: (id: string) => void;
  formatPrice: (price: number) => string;
  buttonText: string;
}

interface SectionHeaderProps {
  title: string;
  categoryId?: string | undefined;
  onViewAll?: ((categoryId: string) => void) | undefined;
  viewAllText: string;
}

// ═══════════════════════════════════════════════════════════════════
// Constants
// ═══════════════════════════════════════════════════════════════════

const DEFAULT_INTERVAL_MS = 3000;

const CATEGORY_SLUGS = {
  production: 'product',
  sets: 'vitaminy-set',
  tech: 'tehnika',
  active: 'active-life',
};

// ═══════════════════════════════════════════════════════════════════
// Helper Functions
// ═══════════════════════════════════════════════════════════════════

function mapApiProductToProduct(api: ApiProduct): Product {
  return {
    id: String(api.id),
    name: api.name,
    description: api.description || '',
    ...mapProductPrice(api),
    imageUrl: api.image_url || '',
    categoryId: api.category_id == null ? '' : String(api.category_id),
    inStock: api.is_in_stock,
    isPurchasable: api.is_purchasable,
    ...mapSizeStock(api),
  };
}

// ═══════════════════════════════════════════════════════════════════
// Sub-components
// ═══════════════════════════════════════════════════════════════════

function ProductCard({
  product,
  index,
  onAddToCart,
  onNavigate,
  formatPrice,
  buttonText,
}: ProductCardProps) {
  const { t } = useTranslation();
  const handleCardClick = useCallback(() => {
    onNavigate(product.id);
  }, [onNavigate, product.id]);

  const handleAddToCart = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      if (isSizedPurchaseBlocked(product)) return;
      if (product.requiresSize) onNavigate(product.id);
      else onAddToCart(product);
    },
    [onAddToCart, onNavigate, product]
  );

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.02 }}
    >
      {/* Card container - без padding, overflow-hidden для обрезки изображения */}
      <div className="neumorphic-flat overflow-hidden h-full flex flex-col">
        {/* Верхняя зона: изображение + информация (кликабельная для перехода к товару) */}
        <div className="cursor-pointer flex-1" onClick={handleCardClick}>
          {/* Изображение - без отступов, от края до края */}
          <div className="aspect-square bg-[var(--bg-tertiary)]">
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
          </div>

          {/* Цена и название */}
          <div className="p-2.5 pb-2">
            {/* Цена */}
            <div className="text-center mb-1">
              {product.isSale && <span className="sale-badge">{t('product.sale')}</span>}
              <div className="text-[var(--primary)] font-bold text-sm">
                {formatPrice(effectivePrice(product))}
              </div>
              {originalPrice(product) != null && (
                <span className="text-[var(--text-muted)] text-xs line-through">
                  {formatPrice(originalPrice(product) ?? 0)}
                </span>
              )}
            </div>

            {/* Название - фиксированная высота для 2 строк */}
            <h3 className="text-xs text-[var(--text-secondary)] text-center line-clamp-2 h-8 leading-4">
              {product.name}
            </h3>
          </div>
        </div>

        {/* Нижняя зона: иконка + текст "В корзину" */}
        {product.isPurchasable !== false && (
          <button type="button" disabled={isSizedPurchaseBlocked(product)}
            onClick={handleAddToCart}
            className="w-full py-2.5 text-[var(--primary)] text-xs font-semibold flex items-center justify-center gap-1.5 cursor-pointer active:opacity-70 transition-opacity border-t border-[var(--border)]/50 disabled:opacity-50"
          >
            <ShoppingCart size={14} />
            {isSizedPurchaseBlocked(product) ? t('product.outOfStock') : buttonText}
          </button>
        )}
      </div>
    </motion.div>
  );
}

function SectionHeader({ title, categoryId, onViewAll, viewAllText }: SectionHeaderProps) {
  const handleViewAll = useCallback(() => {
    if (categoryId && onViewAll) {
      onViewAll(categoryId);
    }
  }, [categoryId, onViewAll]);

  return (
    <div className="flex items-center justify-between mb-3">
      <h2 className="text-lg font-semibold text-[var(--text-primary)]">
        {title}
      </h2>
      {categoryId && onViewAll && (
        <button
          onClick={handleViewAll}
          className="text-[var(--primary)] text-sm font-medium flex items-center gap-1"
        >
          {viewAllText} <ChevronRight size={16} />
        </button>
      )}
    </div>
  );
}

const FAQ_KEYS = [1, 2, 3, 4, 5, 6];

function FAQSection() {
  const { t } = useTranslation();
  const [openIndex, setOpenIndex] = useState<number | null>(null);

  const toggleItem = (index: number) => {
    setOpenIndex(openIndex === index ? null : index);
  };

  return (
    <motion.section
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.5 }}
      className="py-6"
    >
      <h2 className="text-xl font-bold text-[var(--text-primary)] mb-4">
        {t('faq.title')}
      </h2>

      <div className="space-y-2">
        {FAQ_KEYS.map((num, index) => (
          <div
            key={num}
            className="rounded-xl overflow-hidden bg-[var(--bg-surface)] border border-[var(--border)]"
          >
            <button
              onClick={() => toggleItem(index)}
              className="w-full px-4 py-3 flex items-center justify-between text-left gap-3"
            >
              <span className="text-sm font-medium text-[var(--text-primary)]">
                {t(`faq.q${num}`)}
              </span>
              <motion.div
                animate={{ rotate: openIndex === index ? 180 : 0 }}
                transition={{ duration: 0.2 }}
                className="flex-shrink-0"
              >
                <ChevronDown size={18} className="text-[var(--text-muted)]" />
              </motion.div>
            </button>

            <AnimatePresence>
              {openIndex === index && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: 'auto', opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  className="overflow-hidden"
                >
                  <div className="px-4 pb-4 text-sm text-[var(--text-secondary)] leading-relaxed">
                    {t(`faq.a${num}`)}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        ))}
      </div>
    </motion.section>
  );
}

function Footer() {
  const { t } = useTranslation();

  return (
    <footer className="pt-6 mt-4">
      {/* Divider */}
      <div className="border-t border-[var(--border)] mb-6" />

      {/* Contact info - 2 columns on tablet+ */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 text-sm">
        {/* Uzbekistan office */}
        <div className="space-y-1">
          <h3 className="font-semibold text-[var(--text-primary)] mb-2">
            ООО "Biotact"
          </h3>
          <p className="text-[var(--text-secondary)]">{t('footer.uzbekistanCity')}</p>
          <p className="text-[var(--text-secondary)]">{t('footer.uzbekistanStreet')}</p>
          <div className="pt-2 space-y-1">
            <p className="text-[var(--text-secondary)]">
              {t('footer.phone')} <a href="tel:+998935551747" className="text-[var(--primary)]">(+998 93) 555 17 47</a>
            </p>
            <p className="text-[var(--text-secondary)]">
              E-Mail: <a href="mailto:contact_uz@biotact.de" className="text-[var(--primary)]">contact_uz@biotact.de</a>
            </p>
          </div>
        </div>

        {/* Germany office */}
        <div className="space-y-1">
          <h3 className="font-semibold text-[var(--text-primary)] mb-2">
            Biotact Deutschland GmbH
          </h3>
          <p className="text-[var(--text-secondary)]">Siemensstraße 10</p>
          <p className="text-[var(--text-secondary)]">53121 Bonn, Deutschland</p>
          <div className="pt-2 space-y-1">
            <p className="text-[var(--text-secondary)]">
              Tel.: <a href="tel:+4902285048084" className="text-[var(--primary)]">+49 (0) 228 50 48 80 84</a>
            </p>
            <p className="text-[var(--text-secondary)]">
              E-Mail: <a href="mailto:kontakt@biotact.de" className="text-[var(--primary)]">kontakt@biotact.de</a>
            </p>
          </div>
        </div>
      </div>

      {/* Copyright */}
      <div className="mt-6 pt-4 border-t border-[var(--border)] flex items-center justify-between">
        <p className="text-xs text-[var(--text-muted)]">
          © {new Date().getFullYear()} OOO "Biotact"
        </p>
        <a
          href="https://temrjan.com"
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs text-[var(--text-muted)] hover:text-[var(--primary)] transition-colors"
        >
          temrjan.com
        </a>
      </div>
    </footer>
  );
}

// ═══════════════════════════════════════════════════════════════════
// Main Component
// ═══════════════════════════════════════════════════════════════════

export function HomePage() {
  const navigate = useNavigate();
  const { t } = useTranslation();
  const language = useLanguage();

  // Zustand selectors
  const addToCart = useCartStore((state) => state.addItem);

  // Local state
  const [categories, setCategories] = useState<ApiCategory[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [saleProducts, setSaleProducts] = useState<Product[]>([]);
  const [banners, setBanners] = useState<ApiBanner[]>([]);
  const [bannerInterval, setBannerInterval] = useState(DEFAULT_INTERVAL_MS);
  const [loading, setLoading] = useState(true);


  // Fetch data
  useEffect(() => {
    let active = true;
    async function fetchData() {
      setLoading(true);
      try {
        const [catsRes, prodsRes, bannersRes, salesRes] = await Promise.all([
          loadCategories(language),
          loadProducts(language),
          loadActiveBanners(language).catch(() => []),
          loadProducts(language, { on_sale: 'true' }).catch(() => []),
        ]);
        if (!active) return;
        setCategories(catsRes);
        setProducts(prodsRes.map(mapApiProductToProduct));
        setSaleProducts(salesRes.map(mapApiProductToProduct));
        setBanners(bannersRes);

        try {
          const intervalRes = await api.get<{ value: string }>('/settings/banner_interval');
          const parsedInterval = Number(intervalRes.value);
          if (active) {
            setBannerInterval(
              Number.isFinite(parsedInterval) && parsedInterval > 0
                ? parsedInterval
                : DEFAULT_INTERVAL_MS
            );
          }
        } catch {
          // Use default interval
        }
      } catch (error: unknown) {
        console.error('Failed to fetch data:', error);
      } finally {
        if (active) setLoading(false);
      }
    }

    fetchData();
    return () => { active = false; };
  }, [language]);

  // Computed: display banners (no default fallback - show skeleton instead)
  const displayBanners = useMemo(() => {
    return banners;
  }, [banners]);

  const bannersSignature = useMemo(
    () =>
      displayBanners
        .map(
          (b) =>
            `${b.id}:${b.image_url ?? ''}:${b.image_mobile_url ?? ''}:${b.video_url ?? ''}:${b.video_mobile_url ?? ''}`
        )
        .join('|'),
    [displayBanners]
  );

  // State for scroll hint animation
  const [scrollHintX, setScrollHintX] = useState(0);

  // Memoized callbacks
  const formatPrice = useCallback((price: number) => {
    return price.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + ' ' + t('currency.sum');
  }, [t]);

  const handleBannerClick = useCallback(
    (linkUrl: string | null) => {
      if (linkUrl) {
        if (linkUrl.startsWith('http')) {
          window.open(linkUrl, '_blank');
        } else {
          navigate(linkUrl);
        }
      } else {
        navigate('/catalog');
      }
    },
    [navigate]
  );

  const handleNavigateToProduct = useCallback((productId: string) => {
    navigate(`/product/${productId}`);
  }, [navigate]);

  const handleViewAllCategory = useCallback(
    (categoryId: string) => {
      const category = categories.find(c => String(c.id) === categoryId);
      if (category) navigate('/catalog/' + category.slug);
    },
    [navigate, categories]
  );

  // Computed: find categories by slug
  const productionCategory = useMemo(() => {
    return categories.find((c) => c.slug === CATEGORY_SLUGS.production);
  }, [categories]);

  const setsCategory = useMemo(() => {
    return categories.find((c) => c.slug === CATEGORY_SLUGS.sets);
  }, [categories]);

  const techCategory = useMemo(() => {
    return categories.find((c) => c.slug === CATEGORY_SLUGS.tech);
  }, [categories]);

  const activeCategory = useMemo(() => {
    return categories.find((c) => c.slug === CATEGORY_SLUGS.active);
  }, [categories]);

  // Computed: filtered products by category
  const popularProducts = useMemo(() => {
    return products
      .filter(
        (p) =>
          productionCategory && p.categoryId === String(productionCategory.id)
      )
      .slice(0, 8);
  }, [products, productionCategory]);

  // Auto-scroll hint animation for slider
  useEffect(() => {
    if (loading || popularProducts.length === 0) return;

    let timeoutId: ReturnType<typeof setTimeout>;
    let intervalId: ReturnType<typeof setInterval>;

    const doScrollHint = () => {
      setScrollHintX(-80);
      timeoutId = setTimeout(() => {
        setScrollHintX(0);
      }, 1500);
    };

    timeoutId = setTimeout(() => {
      doScrollHint();
      intervalId = setInterval(doScrollHint, 7000);
    }, 1000);

    return () => {
      clearTimeout(timeoutId);
      clearInterval(intervalId);
    };
  }, [loading, popularProducts.length]);

  const setsProducts = useMemo(() => {
    return products
      .filter((p) => setsCategory && p.categoryId === String(setsCategory.id))
      .slice(0, 8);
  }, [products, setsCategory]);

  const techProducts = useMemo(() => {
    return products
      .filter((p) => techCategory && p.categoryId === String(techCategory.id))
      .slice(0, 8);
  }, [products, techCategory]);

  const activeProducts = useMemo(() => {
    return products
      .filter((p) => activeCategory && p.categoryId === String(activeCategory.id))
      .slice(0, 8);
  }, [products, activeCategory]);

  // Button text
  const addToCartText = t('product.addToCart');

  // Early return for loading
  if (loading) {
    return <Loading />;
  }

  // Render
  return (
    <div className="p-3 pt-2 space-y-6 max-w-4xl mx-auto">
      {/* Banner Slider */}
      {displayBanners.length === 0 ? (
        <div className="relative h-32 md:h-72 lg:h-72 overflow-hidden rounded-md">
          {/* Skeleton while banners are loading */}
          <div className="absolute inset-0 bg-[var(--bg-tertiary)] animate-pulse" />
        </div>
      ) : (
        <BannerCarousel
          key={bannersSignature}
          banners={displayBanners}
          language={language}
          intervalMs={bannerInterval}
          onBannerClick={handleBannerClick}
        />
      )}

      {saleProducts.length > 0 && (
        <section aria-label={t('sales.title')}>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">{t('sales.title')}</h2>
            <Link to={ROUTES.SALES} className="text-sm text-[var(--primary)]">{t('sales.viewAll')}</Link>
          </div>
          <div className="flex gap-3 overflow-x-auto pb-2">
            {saleProducts.map((product, index) => (
              <div key={product.id} className="w-[calc(50%-6px)] md:w-[31%] lg:w-[calc(25%-12px)] flex-shrink-0">
                <ProductCard product={product} index={index} onAddToCart={addToCart}
                  onNavigate={handleNavigateToProduct} formatPrice={formatPrice} buttonText={addToCartText} />
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Popular Products */}
      {popularProducts.length > 0 && (
        <motion.section
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
        >
          <SectionHeader
            title={t('home.featuredProducts')}
            categoryId={productionCategory?.id?.toString()}
            onViewAll={handleViewAllCategory}
            viewAllText={t('home.viewAll')}
          />
          <div className="overflow-x-auto pb-2 scrollbar-hide snap-x snap-mandatory">
            <motion.div
              className="flex gap-3 md:gap-4"
              animate={{ x: scrollHintX }}
              transition={{ duration: 0.6, ease: "easeInOut" }}
            >
              {popularProducts.map((product, index) => (
                <div key={product.id} className="w-[calc(50%-6px)] md:w-[31%] lg:w-[calc(25%-12px)] flex-shrink-0 snap-start">
                  <ProductCard
                    product={product}
                    index={index}
                    onAddToCart={addToCart}
                    onNavigate={handleNavigateToProduct}
                    formatPrice={formatPrice}
                    buttonText={addToCartText}
                  />
                </div>
              ))}
            </motion.div>
          </div>
        </motion.section>
      )}

      {/* Sets - horizontal slider */}
      {setsProducts.length > 0 && (
        <motion.section
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
        >
          <SectionHeader
            title={setsCategory?.name || t('home.specialOffers')}
            categoryId={setsCategory?.id?.toString()}
            onViewAll={handleViewAllCategory}
            viewAllText={t('home.viewAll')}
          />
          <div className="overflow-x-auto pb-2 scrollbar-hide snap-x snap-mandatory">
            <div className="flex gap-3 md:gap-4">
              {setsProducts.map((product, index) => (
                <div key={product.id} className="w-[calc(50%-6px)] md:w-[31%] lg:w-[calc(25%-12px)] flex-shrink-0 snap-start">
                  <ProductCard
                    product={product}
                    index={index}
                    onAddToCart={addToCart}
                    onNavigate={handleNavigateToProduct}
                    formatPrice={formatPrice}
                    buttonText={addToCartText}
                  />
                </div>
              ))}
            </div>
          </div>
        </motion.section>
      )}

      {/* Tech for Health - horizontal slider */}
      {techProducts.length > 0 && (
        <motion.section
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 }}
        >
          <SectionHeader
            title={techCategory?.name || t('home.newArrivals')}
            categoryId={techCategory?.id?.toString()}
            onViewAll={handleViewAllCategory}
            viewAllText={t('home.viewAll')}
          />
          <div className="overflow-x-auto pb-2 scrollbar-hide snap-x snap-mandatory">
            <div className="flex gap-3 md:gap-4">
              {techProducts.map((product, index) => (
                <div key={product.id} className="w-[calc(50%-6px)] md:w-[31%] lg:w-[calc(25%-12px)] flex-shrink-0 snap-start">
                  <ProductCard
                    product={product}
                    index={index}
                    onAddToCart={addToCart}
                    onNavigate={handleNavigateToProduct}
                    formatPrice={formatPrice}
                    buttonText={addToCartText}
                  />
                </div>
              ))}
            </div>
          </div>
        </motion.section>
      )}

      {/* Active Lifestyle - horizontal slider */}
      {activeProducts.length > 0 && (
        <motion.section
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.4 }}
        >
          <SectionHeader
            title={activeCategory?.name || 'Активный образ жизни'}
            categoryId={activeCategory?.id?.toString()}
            onViewAll={handleViewAllCategory}
            viewAllText={t('home.viewAll')}
          />
          <div className="overflow-x-auto pb-2 scrollbar-hide snap-x snap-mandatory">
            <div className="flex gap-3 md:gap-4">
              {activeProducts.map((product, index) => (
                <div key={product.id} className="w-[calc(50%-6px)] md:w-[31%] lg:w-[calc(25%-12px)] flex-shrink-0 snap-start">
                  <ProductCard
                    product={product}
                    index={index}
                    onAddToCart={addToCart}
                    onNavigate={handleNavigateToProduct}
                    formatPrice={formatPrice}
                    buttonText={addToCartText}
                  />
                </div>
              ))}
            </div>
          </div>
        </motion.section>
      )}

      {/* FAQ Section */}
      <FAQSection />

      {/* Footer */}
      <Footer />

    </div>
  );
}
