import { effectivePrice } from '@/utils/price';
import { motion, AnimatePresence } from 'framer-motion';
import { Trash2, Minus, Plus, ShoppingBag } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Card } from '@components/ui/Card';
import { useHaptic } from '@hooks/useTelegram';
import { useTranslation } from '@/i18n';
import {
  useCartStore,
  useCartItems,
  useCartTotalAmount,
  useRefreshCartPrices,
} from '@stores/cartStore';

// ═══════════════════════════════════════════════════════════════════
// CartPage Component
// ═══════════════════════════════════════════════════════════════════

export function CartPage() {
  const navigate = useNavigate();
  const haptic = useHaptic();
  const { t } = useTranslation();
  const items = useCartItems();
  useRefreshCartPrices();
  const totalAmount = useCartTotalAmount();
  const { updateQuantity, removeItem, clearCart } = useCartStore();

  const formatPrice = (price: number) => {
    return new Intl.NumberFormat('ru-RU').format(price) + ' ' + t('currency.sum');
  };

  const handleCheckout = async () => {
    haptic.success();
    navigate('/checkout');
  };

  const handleQuantityChange = (productId: string, delta: number) => {
    haptic.selection();
    const item = items.find((i) => i.productId === productId);
    if (item) {
      const newQuantity = item.quantity + delta;
      updateQuantity(productId, newQuantity);
    }
  };

  const handleRemove = (productId: string) => {
    haptic.warning();
    removeItem(productId);
  };

  if (items.length === 0) {
    return (
      <div className="p-3 flex flex-col items-center justify-center min-h-[60vh] max-w-2xl mx-auto">
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          className="text-center"
        >
          <div className="neumorphic w-24 h-24 rounded-full flex items-center justify-center mx-auto mb-4">
            <ShoppingBag size={40} className="text-[var(--text-muted)]" />
          </div>
          <h2 className="text-xl font-bold mb-2">{t('cart.empty')}</h2>
          <p className="text-[var(--text-muted)] mb-4">
            {t('cart.emptyDescription')}
          </p>
          <button
            onClick={() => navigate('/catalog')}
            className="h-12 px-6 bg-[var(--primary)] text-white font-semibold rounded-xl"
          >
            {t('cart.goToCatalog')}
          </button>
        </motion.div>
      </div>
    );
  }

  return (
    <div className="p-3 space-y-4 pb-6 max-w-2xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">{t('cart.title')}</h1>
        <button
          onClick={() => {
            haptic.warning();
            clearCart();
          }}
          className="text-[var(--error)] text-sm font-medium"
        >
          {t('cart.clear')}
        </button>
      </div>

      {/* Cart Items */}
      <AnimatePresence mode="popLayout">
        {items.map((item) => (
          <motion.div
            key={item.productId}
            layout
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 20 }}
          >
            <Card className="flex gap-4">
              {/* Product Image */}
              <div className="w-20 h-20 rounded-xl overflow-hidden flex-shrink-0 bg-[var(--bg-secondary)]">
                {item.product.imageUrl ? (
                  <img
                    src={item.product.imageUrl}
                    alt={item.product.name}
                    className="w-full h-full object-cover"
                  />
                ) : (
                  <div className="w-full h-full flex items-center justify-center">
                    <ShoppingBag size={24} className="text-[var(--text-muted)]" />
                  </div>
                )}
              </div>

              {/* Product Info */}
              <div className="flex-1 min-w-0">
                <h3 className="font-medium truncate">{item.product.name}</h3>
                {item.product.isSale && <span className="sale-badge">{t('product.sale')}</span>}
                {item.product.isSale && <p className="text-[var(--text-muted)] text-sm line-through">{formatPrice(item.product.price)}</p>}
                <p className="text-[var(--primary)] font-bold">
                  {formatPrice(effectivePrice(item.product))}
                </p>

                {/* Quantity Controls */}
                <div className="flex items-center justify-between mt-2">
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => handleQuantityChange(item.productId, -1)}
                      className="neumorphic-sm w-8 h-8 flex items-center justify-center rounded-full"
                    >
                      <Minus size={14} />
                    </button>
                    <span className="w-6 text-center font-medium">{item.quantity}</span>
                    <button
                      onClick={() => handleQuantityChange(item.productId, 1)}
                      className="neumorphic-sm w-8 h-8 flex items-center justify-center rounded-full"
                    >
                      <Plus size={14} />
                    </button>
                  </div>

                  <button
                    onClick={() => handleRemove(item.productId)}
                    className="text-[var(--error)] p-2"
                  >
                    <Trash2 size={18} />
                  </button>
                </div>
              </div>
            </Card>
          </motion.div>
        ))}
      </AnimatePresence>

      {/* Total */}
      <Card className="p-4">
        <div className="flex items-center justify-between mb-4">
          <span className="text-[var(--text-secondary)]">{t('cart.total')}:</span>
          <span className="text-xl font-bold text-[var(--primary)]">
            {formatPrice(totalAmount)}
          </span>
        </div>

        {/* Checkout Button - стиль Telegram MainButton */}
        <motion.button
          whileTap={{ scale: 0.98 }}
          onClick={handleCheckout}
          className="w-full h-12 bg-[#2a6b50] text-white font-semibold rounded-xl text-lg"
        >
          {t('cart.checkout')}
        </motion.button>
      </Card>
    </div>
  );
}
