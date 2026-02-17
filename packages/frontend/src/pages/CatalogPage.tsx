import { useState, useEffect, useMemo, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ShoppingCart, ChevronDown } from 'lucide-react';
import { api } from '@/api/client';
import { Card } from '@/components/ui/Card';
import { Loading } from '@/components/ui/Loading';
import { useCartStore } from '@/stores/cartStore';
import { useHaptic } from '@/hooks/useTelegram';
import { useTranslation } from '@/i18n';
import { useLanguage } from '@/stores/languageStore';
import type { Product, Category } from '@/types';

interface ApiCategory {
  id: number;
  name: string;
  name_uz: string | null;
  slug: string;
  image_url: string | null;
}

interface ApiProduct {
  id: number;
  name: string;
  name_uz: string | null;
  slug: string;
  description: string | null;
  price: number;
  old_price: number | null;
  image_url: string | null;
  category_id: number;
  stock_quantity: number;
  is_purchasable: boolean;
}

function mapCategory(api: ApiCategory): Category {
  return {
    id: String(api.id),
    name: api.name,
    slug: api.slug,
    imageUrl: api.image_url || undefined,
    order: 0,
  };
}

function mapProduct(api: ApiProduct): Product {
  return {
    id: String(api.id),
    name: api.name,
    description: api.description || '',
    price: api.price,
    oldPrice: api.old_price || undefined,
    imageUrl: api.image_url || '',
    categoryId: String(api.category_id),
    inStock: api.stock_quantity > 0,
    isPurchasable: api.is_purchasable,
    quantity: api.stock_quantity,
  };
}

const PRODUCTS_PER_PAGE = 20;

// Типы для ProductCard
interface ProductCardProps {
  product: Product;
  onAddToCart: (product: Product) => void;
  onProductClick: (productId: string) => void;
  formatPrice: (price: number) => string;
  buttonText: string;
}

// Компонент карточки товара (такой же дизайн как на HomePage)
function ProductCard({
  product,
  onAddToCart,
  onProductClick,
  formatPrice,
  buttonText,
}: ProductCardProps) {
  const handleCardClick = useCallback(() => {
    onProductClick(product.id);
  }, [onProductClick, product.id]);

  const handleAddToCart = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      onAddToCart(product);
    },
    [onAddToCart, product]
  );

  return (
    <div>
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
              <div className="text-[var(--primary)] font-bold text-sm">
                {formatPrice(product.price)}
              </div>
              {product.oldPrice && (
                <span className="text-[var(--text-muted)] text-xs line-through">
                  {formatPrice(product.oldPrice)}
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
          <div
            onClick={handleAddToCart}
            className="w-full py-2.5 text-[var(--primary)] text-xs font-semibold flex items-center justify-center gap-1.5 cursor-pointer active:opacity-70 transition-opacity border-t border-[var(--border)]/50"
          >
            <ShoppingCart size={14} />
            {buttonText}
          </div>
        )}
      </div>
    </div>
  );
}

export function CatalogPage() {
  const { slug } = useParams<{ slug?: string }>();
  const navigate = useNavigate();
  const haptic = useHaptic();
  const { t } = useTranslation();
  const language = useLanguage();

  // Redirect ramadan-set to promo page
  useEffect(() => {
    if (slug === 'ramadan-set') {
      navigate('/ramadan', { replace: true });
    }
  }, [slug, navigate]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [visibleCount, setVisibleCount] = useState(PRODUCTS_PER_PAGE);
  const addToCart = useCartStore((state) => state.addItem);

  useEffect(() => {
    async function fetchData() {
      setLoading(true);
      try {
        const [catsRes, prodsRes] = await Promise.all([
          api.get<{ items: ApiCategory[] }>('/categories'),
          api.get<{ items: ApiProduct[] }>('/products?size=100'),
        ]);
        setCategories((catsRes.items || []).map(mapCategory));
        setProducts((prodsRes.items || []).map(mapProduct));
      } catch (err) {
        console.error('Failed to fetch data:', err);
      } finally {
        setLoading(false);
      }
    }
    fetchData();
  }, [language]);

  // Reset visible count when category changes
  useEffect(() => {
    setVisibleCount(PRODUCTS_PER_PAGE);
  }, [slug]);

  const selectedCategory = useMemo(() => {
    if (!slug) return null;
    return categories.find((c) => c.slug === slug) || null;
  }, [categories, slug]);

  const filteredProducts = useMemo(() => {
    if (!selectedCategory) return products;
    return products.filter((p) => p.categoryId === selectedCategory.id);
  }, [products, selectedCategory]);

  const visibleProducts = useMemo(() => {
    return filteredProducts.slice(0, visibleCount);
  }, [filteredProducts, visibleCount]);

  const hasMore = visibleCount < filteredProducts.length;

  const formatPrice = useCallback(
    (price: number) =>
      Math.floor(price).toLocaleString('ru-RU').replace(/,/g, ' ') + ' ' + t('currency.sum'),
    [t]
  );

  const handleCategorySelect = (categorySlug: string | null) => {
    haptic.selection();
    if (categorySlug) {
      navigate('/catalog/' + categorySlug);
    } else {
      navigate('/catalog');
    }
  };

  const handleShowMore = () => {
    haptic.light();
    setVisibleCount((prev) => prev + PRODUCTS_PER_PAGE);
  };

  const handleAddToCart = useCallback(
    (product: Product) => {
      haptic.light();
      addToCart(product);
    },
    [haptic, addToCart]
  );

  const handleProductClick = useCallback((productId: string) => {
    navigate(`/product/${productId}`);
  }, [navigate]);

  const buttonText = t('product.addToCart');

  if (loading) return <Loading />;

  return (
    <div className="p-3 pt-2 md:pt-3 space-y-4 max-w-4xl mx-auto">
      {/* Categories - simple 2x2 grid */}
      <section>
        <div className="flex flex-col gap-2">
          {categories.map((category) => {
            const isSelected = selectedCategory?.id === category.id;
            return (
              <button
                key={category.id}
                onClick={() => handleCategorySelect(isSelected ? null : category.slug)}
                className={`
                  px-4 py-3 rounded-xl text-left transition-all
                  ${isSelected
                    ? 'bg-[var(--primary)] text-white'
                    : 'bg-[var(--bg-surface)] text-[var(--text-primary)] border border-[var(--border)] hover:border-[var(--primary)]'
                  }
                `}
              >
                <span className="text-sm font-medium truncate">{category.name}</span>
              </button>
            );
          })}
        </div>
      </section>

      {/* Products */}
      <section>
        <h2 className="text-lg font-semibold mb-3">
          {selectedCategory ? selectedCategory.name : t('catalog.allProducts')}
          <span className="text-sm font-normal text-[var(--text-muted)] ml-2">
            ({filteredProducts.length})
          </span>
        </h2>

        {filteredProducts.length === 0 ? (
          <Card className="p-8 text-center">
            <p className="text-[var(--text-muted)]">{t('catalog.noProducts')}</p>
          </Card>
        ) : (
          <>
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
              {visibleProducts.map((product) => (
                <ProductCard
                  key={product.id}
                  product={product}
                  onAddToCart={handleAddToCart}
                  onProductClick={handleProductClick}
                  formatPrice={formatPrice}
                  buttonText={buttonText}
                />
              ))}
            </div>

            {/* Show More Button */}
            {hasMore && (
              <div className="flex justify-center mt-4">
                <button
                  onClick={handleShowMore}
                  className="flex items-center gap-2 px-6 py-3 rounded-xl bg-[var(--bg-secondary)] hover:bg-[var(--bg-tertiary)] text-[var(--text-primary)] font-medium transition-colors"
                >
                  <span>{t('home.showMore')}</span>
                  <ChevronDown size={18} />
                </button>
              </div>
            )}
          </>
        )}
      </section>

    </div>
  );
}
