import { isSizedPurchaseBlocked, mapSizeStock } from '@/utils/size';
import { effectivePrice, originalPrice, mapProductPrice } from '@/utils/price';
import { useState, useEffect, type ReactNode } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { ShoppingCart, ChevronLeft, ChevronRight, ArrowLeft } from 'lucide-react';
import { Loading } from '@/components/ui/Loading';
import { useCartStore } from '@/stores/cartStore';
import { useHaptic } from '@/hooks/useTelegram';
import { useBackButton } from '@/hooks/useBackButton';
import { useTranslation } from '@/i18n';
import { useLanguage } from '@/stores/languageStore';
import { productsApi } from '@/api';
import type { CatalogueProduct as ApiProduct } from '@/api/catalogue';
import type { Product } from '@/types';
import { SizeSelector } from '@/components/features/SizeSelector';

/** Parse **bold** markdown into React nodes */
function parseBold(text: string): ReactNode[] {
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    return part;
  });
}


interface ExtendedProduct extends Product {
  additionalInfoTitle?: string | undefined;
  additionalInfo?: string | undefined;
  disclaimer?: string | undefined;
  youtubeVideoUrl?: string | undefined;
}

function mapProduct(api: ApiProduct): ExtendedProduct {
  return {
    id: String(api.id),
    name: api.name,
    description: api.description || '',
    ...mapProductPrice(api),
    imageUrl: api.image_url || '',
    images: api.images || undefined,
    categoryId: api.category_id == null ? '' : String(api.category_id),
    inStock: api.requires_size ? api.is_in_stock : true,
    isPurchasable: api.is_purchasable,
    ...mapSizeStock(api),
    quantity: api.stock_quantity ?? undefined,
    additionalInfoTitle: api.additional_info_title || undefined,
    additionalInfo: api.additional_info || undefined,
    disclaimer: api.disclaimer || undefined,
    youtubeVideoUrl: api.video_url || undefined,
  };
}

function getYouTubeEmbedUrl(url: string): string | null {
  try {
    const videoId = url.match(/(?:youtube\.com\/(?:watch\?v=|embed\/)|youtu\.be\/)([^&?/\s]+)/)?.[1];
    return videoId ? 'https://www.youtube.com/embed/' + videoId : null;
  } catch {
    return null;
  }
}

export function ProductPage() {
  const { productId } = useParams<{ productId: string }>();
  const navigate = useNavigate();
  const haptic = useHaptic();
  const { t } = useTranslation();
  const language = useLanguage();
  const { addItem } = useCartStore();

  const [product, setProduct] = useState<ExtendedProduct | null>(null);
  const [similarProducts, setSimilarProducts] = useState<Product[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [currentImageIndex, setCurrentImageIndex] = useState(0);
  const [selectedSize, setSelectedSize] = useState<string | null>(null);
  if (selectedSize && !product?.availableSizes?.includes(selectedSize)) setSelectedSize(null);

  useBackButton();

  const handleGoBack = () => {
    haptic.light();
    navigate(-1);
  };

  const formatPrice = (price: number) => {
    return new Intl.NumberFormat('ru-RU').format(price) + ' ' + t('currency.sum');
  };

  useEffect(() => {
    let active = true;
    async function fetchProduct() {
      if (!productId) return;
      setIsLoading(true);
      setError(null);
      try {
        const response = await productsApi.getProduct(productId, language);
        if (!active) return;
        const mappedProduct = mapProduct(response);
        setProduct(mappedProduct);
        setSelectedSize(null);
        setSimilarProducts([]);
        setCurrentImageIndex(0);
        if (mappedProduct.categoryId) {
          const similarResponse = await productsApi.getProducts({
            categoryId: mappedProduct.categoryId,
            limit: 10,
          }, language);
          if (!active) return;
          const mapped = similarResponse.items.filter((p) => String(p.id) !== productId && p.category_id === response.category_id).slice(0, 5).map(mapProduct);
          setSimilarProducts(mapped);
        }
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : 'Failed to load product');
      } finally {
        if (active) setIsLoading(false);
      }
    }
    fetchProduct();
    return () => { active = false; };
  }, [productId, language]);

  const handleAddToCart = () => {
    if (product && product.inStock && product.isPurchasable !== false && !isSizedPurchaseBlocked(product) && (!product.requiresSize || selectedSize)) {
      haptic.success();
      addItem(product, 1, selectedSize);
    }
  };

  const handleImageChange = (direction: 'next' | 'prev') => {
    if (!product?.images) return;
    haptic.selection();
    setCurrentImageIndex((prev) => {
      if (direction === 'next') {
        return prev === product.images!.length - 1 ? 0 : prev + 1;
      }
      return prev === 0 ? product.images!.length - 1 : prev - 1;
    });
  };

  const handleSimilarProductClick = (id: string) => {
    haptic.light();
    navigate('/product/' + id);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  if (isLoading) {
    return <Loading />;
  }

  if (error || !product) {
    return (
      <div className="p-4 text-center">
        <p className="text-[var(--error)]">{error || t('product.notFound')}</p>
      </div>
    );
  }

  const images = product.images && product.images.length > 0 ? product.images : [product.imageUrl];
  const embedUrl = product.youtubeVideoUrl ? getYouTubeEmbedUrl(product.youtubeVideoUrl) : null;

  return (
    <div className="pt-safe-top md:pt-4 pb-6 max-w-5xl mx-auto">

      {/* Back button */}
      <button
        onClick={handleGoBack}
        className="flex items-center gap-1 text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors px-4 py-2 md:px-6 lg:px-6"
      >
        <ArrowLeft size={20} />
        <span className="text-sm">{t('common.back')}</span>
      </button>

      <div className="lg:grid lg:grid-cols-2 lg:gap-8 lg:p-6">
        {/* Image gallery */}
        <div>
          <div className="relative w-full aspect-square bg-[var(--bg-secondary)] rounded-xl overflow-hidden">
            <AnimatePresence mode="wait">
              <motion.img
                key={currentImageIndex}
                src={images[currentImageIndex]}
                alt={product.name}
                className="w-full h-full object-cover"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.3 }}
              />
            </AnimatePresence>
            {images.length > 1 && (
              <>
                <button onClick={() => handleImageChange('prev')} className="absolute left-4 top-1/2 -translate-y-1/2 neumorphic-sm w-10 h-10 flex items-center justify-center rounded-full lg:hidden">
                  <ChevronLeft size={20} />
                </button>
                <button onClick={() => handleImageChange('next')} className="absolute right-4 top-1/2 -translate-y-1/2 neumorphic-sm w-10 h-10 flex items-center justify-center rounded-full lg:hidden">
                  <ChevronRight size={20} />
                </button>
              </>
            )}
            {images.length > 1 && (
              <div className="absolute bottom-4 left-1/2 -translate-x-1/2 flex gap-2 lg:hidden">
                {images.map((_, index) => (
                  <div key={index} className={'w-2 h-2 rounded-full transition-all ' + (index === currentImageIndex ? 'bg-[var(--primary)] w-6' : 'bg-white/50')} />
                ))}
              </div>
            )}
          </div>
          {/* Desktop thumbnails */}
          {images.length > 1 && (
            <div className="hidden lg:flex gap-2 mt-3">
              {images.map((src, index) => (
                <button
                  key={index}
                  onClick={() => setCurrentImageIndex(index)}
                  className={'w-16 h-16 rounded-lg overflow-hidden border-2 transition-all flex-shrink-0 ' + (index === currentImageIndex ? 'border-[var(--primary)] opacity-100' : 'border-transparent opacity-60 hover:opacity-100')}
                >
                  <img src={src} alt={product.name + ' ' + (index + 1)} className="w-full h-full object-cover" />
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Product info — right column on desktop */}
        <div className="p-4 md:p-6 lg:p-0 space-y-4">
          <h1 className="text-2xl font-bold">{product.name}</h1>
          {product.isSale && <span className="sale-badge">{t('product.sale')}</span>}
          <div className="flex items-baseline gap-3">
            <div className="text-3xl font-bold text-[var(--primary)]">{formatPrice(effectivePrice(product))}</div>
            {originalPrice(product) != null && (
              <div className="text-lg text-[var(--text-secondary)] line-through">{formatPrice(originalPrice(product) ?? 0)}</div>
            )}
          </div>
          {product.isPurchasable !== false && (
            <>
            {product.requiresSize && <SizeSelector sizes={product.availableSizes ?? []} selected={selectedSize} onChange={setSelectedSize} />}
            <motion.button whileTap={{ scale: 0.98 }} onClick={handleAddToCart} disabled={!product.inStock || isSizedPurchaseBlocked(product) || (product.requiresSize && !selectedSize)} className="w-full h-12 bg-[var(--primary)] text-white font-semibold rounded-xl text-lg flex items-center justify-center gap-2 disabled:opacity-50">
              <ShoppingCart size={20} />
              {t('product.addToCart')}
            </motion.button>
            </>
          )}
          <div className="h-px bg-[var(--border)]" />
          {product.description && (
            <>
              <div>
                <h2 className="text-lg font-bold mb-2">{t('product.description')}</h2>
                <p className="text-[var(--text-secondary)] whitespace-pre-wrap">{parseBold(product.description)}</p>
              </div>
              <div className="h-px bg-[var(--border)]" />
            </>
          )}
        </div>
      </div>

      {/* Full-width sections below the grid */}
      <div className="p-4 md:p-6 space-y-4">
        {product.additionalInfo && (
          <>
            <div>
              <h2 className="text-lg font-bold mb-2">{product.additionalInfoTitle || t('product.information')}</h2>
              <p className="text-[var(--text-secondary)] whitespace-pre-wrap">{parseBold(product.additionalInfo)}</p>
            </div>
            <div className="h-px bg-[var(--border)]" />
          </>
        )}
        {product.disclaimer && (
          <>
            <div className="bg-[var(--warning)]/10 border border-[var(--warning)]/30 rounded-xl p-4">
              <p className="text-sm text-[var(--text-secondary)]">{product.disclaimer}</p>
            </div>
            <div className="h-px bg-[var(--border)]" />
          </>
        )}
        {embedUrl && (
          <>
            <div>
              <div className="aspect-video rounded-xl overflow-hidden">
                <iframe src={embedUrl} title="Product video" className="w-full h-full" referrerPolicy="strict-origin-when-cross-origin" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowFullScreen />
              </div>
            </div>
            <div className="h-px bg-[var(--border)]" />
          </>
        )}

        {similarProducts.length > 0 && (
          <div>
            <h2 className="text-lg font-bold mb-3">{t('product.similar')}</h2>
            <div className="flex gap-3 overflow-x-auto pb-2 -mx-4 px-4 md:-mx-6 md:px-6 scrollbar-hide">
              {similarProducts.map((item) => (
                <motion.div key={item.id} whileTap={{ scale: 0.95 }} onClick={() => handleSimilarProductClick(item.id)} className="flex-shrink-0 w-32 md:w-40 cursor-pointer">
                  <div className="neumorphic-flat overflow-hidden rounded-xl">
                    <div className="aspect-square bg-[var(--bg-secondary)]">
                      <img src={item.imageUrl} alt={item.name} className="w-full h-full object-cover" />
                    </div>
                    <div className="p-2">
                      <p className="text-sm font-medium line-clamp-2 mb-1">{item.name}</p>
                      {item.isSale && <span className="sale-badge">{t('product.sale')}</span>}
                      <p className="text-[var(--primary)] font-bold text-sm">{formatPrice(effectivePrice(item))}</p>
                      {item.isSale && <span className="text-[var(--text-muted)] text-xs line-through">{formatPrice(item.price)}</span>}
                    </div>
                  </div>
                </motion.div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
