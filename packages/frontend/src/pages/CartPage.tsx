import { effectivePrice } from '@/utils/price';
import { motion, AnimatePresence } from 'framer-motion';
import { Trash2, Minus, Plus, ShoppingBag } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Card } from '@components/ui/Card';
import { useHaptic } from '@hooks/useTelegram';
import { useTranslation } from '@/i18n';
import { cartLineKey, isSizedPurchaseBlocked, needsSizeChoice } from '@/utils/size';
import { SizeSelector } from '@/components/features/SizeSelector';
import { usePendingOrderStore } from '@/stores/pendingOrderStore';
import { useEventPromotion } from '@hooks/useEventPromotion';
import { cartDiscount } from '@/utils/eventPromotion';
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
  const cartTotal = useCartTotalAmount();
  // Event discount (R-51): the cart mirrors the server's R-10 rule for the same catalogue prices.
  const { data: promotion } = useEventPromotion();
  const promoted = promotion ? cartDiscount(items, promotion.percent) : null;
  const totalAmount = promoted && promoted.discount > 0 ? promoted.total : cartTotal;
  const { updateQuantity, removeItem, clearCart, chooseSize } = useCartStore();
  const recovering = usePendingOrderStore((state) => state.creationUnknown || state.pending?.stockManaged === true);
  const blocked = !recovering && items.some((item) => needsSizeChoice(item) || isSizedPurchaseBlocked(item.product));

  const formatPrice = (price: number) => {
    return new Intl.NumberFormat('ru-RU').format(price) + ' ' + t('currency.sum');
  };

  const handleCheckout = async () => {
    haptic.success();
    navigate(recovering ? '/orders' : '/checkout');
  };

  const handleQuantityChange = (productId: string, delta: number, size: string | null) => {
    haptic.selection();
    const item = items.find((i) => i.productId === productId && (i.size ?? null) === size);
    if (item) {
      const newQuantity = item.quantity + delta;
      updateQuantity(productId, newQuantity, size);
    }
  };

  const handleRemove = (productId: string, size: string | null) => {
    haptic.warning();
    removeItem(productId, size);
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
            key={cartLineKey(item)}
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
                {item.size && <p className="text-sm">{t('product.size')}: {item.size}</p>}
                {item.product.requiresSize && <SizeSelector sizes={item.product.availableSizes ?? []}
                  selected={needsSizeChoice(item) ? null : item.size ?? null}
                  onChange={(size) => chooseSize(item.productId, item.size ?? null, size)} />}
                {item.product.isSale && <span className="sale-badge">{t('product.sale')}</span>}
                {item.product.isSale && <p className="text-[var(--text-muted)] text-sm line-through">{formatPrice(item.product.price)}</p>}
                <p className="text-[var(--primary)] font-bold">
                  {formatPrice(effectivePrice(item.product))}
                </p>

                {/* Quantity Controls */}
                <div className="flex items-center justify-between mt-2">
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => handleQuantityChange(item.productId, -1, item.size ?? null)}
                      className="neumorphic-sm w-8 h-8 flex items-center justify-center rounded-full"
                    >
                      <Minus size={14} />
                    </button>
                    <span className="w-6 text-center font-medium">{item.quantity}</span>
                    <button
                      onClick={() => handleQuantityChange(item.productId, 1, item.size ?? null)}
                      className="neumorphic-sm w-8 h-8 flex items-center justify-center rounded-full"
                    >
                      <Plus size={14} />
                    </button>
                  </div>

                  <button
                    onClick={() => handleRemove(item.productId, item.size ?? null)}
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
        {promotion && promoted && promoted.discount > 0 && (
          <p className="mb-2 text-sm font-medium text-[var(--primary)]">
            {t('eventPromotion.cartDiscount', {
              title: promotion.title,
              p: promotion.percent,
              amount: formatPrice(promoted.discount),
            })}
          </p>
        )}
        {promoted?.hasUndiscounted && (
          <p className="mb-2 text-xs text-[var(--text-muted)]">{t('eventPromotion.cartHint')}</p>
        )}
        <div className="flex items-center justify-between mb-4">
          <span className="text-[var(--text-secondary)]">{t('cart.total')}:</span>
          <span className="text-xl font-bold text-[var(--primary)]">
            {formatPrice(totalAmount)}
          </span>
        </div>

        {/* Checkout Button - стиль Telegram MainButton */}
        <motion.button
          disabled={blocked}
          whileTap={{ scale: 0.98 }}
          onClick={handleCheckout}
          className="w-full h-12 bg-[var(--primary)] text-white font-semibold rounded-xl text-lg"
        >
          {t(recovering ? 'profile.myOrders' : 'cart.checkout')}
        </motion.button>
      </Card>
    </div>
  );
}
