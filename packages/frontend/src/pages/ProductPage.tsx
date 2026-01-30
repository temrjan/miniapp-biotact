import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { ShoppingCart, ChevronLeft, ChevronRight } from 'lucide-react';
import { Loading } from '@/components/ui/Loading';
import { useCartStore } from '@/stores/cartStore';
import { useHaptic } from '@/hooks/useTelegram';
import { useTranslation } from '@/i18n';
import { useLanguage } from '@/stores/languageStore';
import { productsApi } from '@/api';
import type { Product } from '@/types';

interface ApiProduct {
  id: number;
  name: string;
  name_uz: string | null;
  slug: string;
  description: string | null;
  description_uz: string | null;
  price: number;
  old_price: number | null;
  image_url: string | null;
  images: string[] | null;
  category_id: number;
  stock_quantity: number;
  is_purchasable: boolean;
  additional_info_title: string | null;
  additional_info_title_uz: string | null;
  additional_info: string | null;
  additional_info_uz: string | null;
  disclaimer: string | null;
  disclaimer_uz: string | null;
  video_url: string | null;
}

interface ExtendedProduct extends Product {
  additionalInfoTitle?: string;
  additionalInfo?: string;
  disclaimer?: string;
  youtubeVideoUrl?: string;
}

function mapProduct(api: ApiProduct, lang: string): ExtendedProduct {
  const isUz = lang === 'uz';
  return {
    id: String(api.id),
    name: isUz && api.name_uz ? api.name_uz : api.name,
    description: isUz && api.description_uz ? api.description_uz : (api.description || ''),
    price: api.price,
    oldPrice: api.old_price || undefined,
    imageUrl: api.image_url || '',
    images: api.images || undefined,
    categoryId: String(api.category_id),
    inStock: true,
    isPurchasable: api.is_purchasable,
    quantity: api.stock_quantity,
    additionalInfoTitle: isUz && api.additional_info_title_uz ? api.additional_info_title_uz : api.additional_info_title || undefined,
    additionalInfo: isUz && api.additional_info_uz ? api.additional_info_uz : api.additional_info || undefined,
    disclaimer: isUz && api.disclaimer_uz ? api.disclaimer_uz : api.disclaimer || undefined,
    youtubeVideoUrl: api.video_url || undefined,
  };
}

function getYouTubeEmbedUrl(url: string): string | null {
  try {
    const videoId = url.match(/(?:youtube\.com\/(?:watch\?v=|embed\/)|youtu\.be\/)([^&?\/\s]+)/)?.[1];
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

  const formatPrice = (price: number) => {
    return new Intl.NumberFormat('ru-RU').format(price) + ' ' + t('currency.sum');
  };

  useEffect(() => {
    async function fetchProduct() {
      if (!productId) return;
      setIsLoading(true);
      setError(null);
      try {
        const response = await productsApi.getProduct(productId);
        const mappedProduct = mapProduct(response as unknown as ApiProduct, language);
        setProduct(mappedProduct);
        if (mappedProduct.categoryId) {
          const similarResponse = await productsApi.getProducts({
            categoryId: mappedProduct.categoryId,
            limit: 10,
          });
          const mapped = similarResponse.items.filter((p: any) => p.id !== productId).slice(0, 5).map((p: any) => ({ id: p.id, name: p.name, description: p.description || "", price: p.price, oldPrice: p.old_price, imageUrl: p.image_url || "", categoryId: p.category_id.toString(), inStock: p.stock_quantity > 0 }));
          setSimilarProducts(mapped);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load product');
      } finally {
        setIsLoading(false);
      }
    }
    fetchProduct();
  }, [productId, language]);

  const handleAddToCart = () => {
    if (product) {
      haptic.success();
      addItem(product);
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
    <div className="pt-safe-top md:pt-4 pb-6 max-w-4xl mx-auto">

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
            <button onClick={() => handleImageChange('prev')} className="absolute left-4 top-1/2 -translate-y-1/2 neumorphic-sm w-10 h-10 flex items-center justify-center rounded-full">
              <ChevronLeft size={20} />
            </button>
            <button onClick={() => handleImageChange('next')} className="absolute right-4 top-1/2 -translate-y-1/2 neumorphic-sm w-10 h-10 flex items-center justify-center rounded-full">
              <ChevronRight size={20} />
            </button>
          </>
        )}
        {images.length > 1 && (
          <div className="absolute bottom-4 left-1/2 -translate-x-1/2 flex gap-2">
            {images.map((_, index) => (
              <div key={index} className={'w-2 h-2 rounded-full transition-all ' + (index === currentImageIndex ? 'bg-[var(--primary)] w-6' : 'bg-white/50')} />
            ))}
          </div>
        )}
      </div>

      <div className="p-4 md:p-6 space-y-4">
        <div className="text-3xl font-bold text-[var(--primary)]">{formatPrice(product.price)}</div>
        <h1 className="text-2xl font-bold">{product.name}</h1>
        {product.isPurchasable !== false && (
          <motion.button whileTap={{ scale: 0.98 }} onClick={handleAddToCart} className="w-full h-12 bg-[#2a6b50] text-white font-semibold rounded-xl text-lg flex items-center justify-center gap-2">
            <ShoppingCart size={20} />
            {t("product.addToCart")}
          </motion.button>
        )}
        <div className="h-px bg-[var(--border)]" />
        {product.description && (
          <>
            <div>
              <h2 className="text-lg font-semibold mb-2">{t('product.description')}</h2>
              <p className="text-[var(--text-secondary)] whitespace-pre-wrap">{product.description}</p>
            </div>
            <div className="h-px bg-[var(--border)]" />
          </>
        )}
        {product.additionalInfo && (
          <>
            <div>
              <h2 className="text-lg font-semibold mb-2">{product.additionalInfoTitle || t('product.information')}</h2>
              <p className="text-[var(--text-secondary)] whitespace-pre-wrap">{product.additionalInfo}</p>
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
                <iframe src={embedUrl} title="Product video" className="w-full h-full" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowFullScreen />
              </div>
            </div>
            <div className="h-px bg-[var(--border)]" />
          </>
        )}

        {similarProducts.length > 0 && (
          <div>
            <h2 className="text-lg font-semibold mb-3">{t('product.similar')}</h2>
            <div className="flex gap-3 overflow-x-auto pb-2 -mx-4 px-4 md:-mx-6 md:px-6 scrollbar-hide">
              {similarProducts.map((item) => (
                <motion.div key={item.id} whileTap={{ scale: 0.95 }} onClick={() => handleSimilarProductClick(item.id)} className="flex-shrink-0 w-32 md:w-40 cursor-pointer">
                  <div className="neumorphic-flat overflow-hidden rounded-xl">
                    <div className="aspect-square bg-[var(--bg-secondary)]">
                      <img src={item.imageUrl} alt={item.name} className="w-full h-full object-cover" />
                    </div>
                    <div className="p-2">
                      <p className="text-sm font-medium line-clamp-2 mb-1">{item.name}</p>
                      <p className="text-[var(--primary)] font-bold text-sm">{formatPrice(item.price)}</p>
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
