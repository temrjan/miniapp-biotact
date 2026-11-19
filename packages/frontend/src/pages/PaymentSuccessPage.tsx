import { useEffect } from 'react';
import { motion } from 'framer-motion';
import { CheckCircle, Clock, Home, Package } from 'lucide-react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Card } from '@components/ui/Card';
import { Button } from '@components/ui/Button';
import { useHaptic } from '@hooks/useTelegram';
import { useCartStore } from '@stores/cartStore';
import { useTranslation } from '@/i18n';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api/client';
import { cartMatchesOrder } from '@/utils/order';
import { usePendingOrderStore } from '@/stores/pendingOrderStore';

interface ConfirmedOrder {
  payment_status: string;
  items: { id: number; product_id: number | null; product_name: string; size: string | null; quantity: number }[];
}

// ═══════════════════════════════════════════════════════════════════
// PaymentSuccessPage Component
// ═══════════════════════════════════════════════════════════════════

export function PaymentSuccessPage() {
  const navigate = useNavigate();
  const haptic = useHaptic();
  const { t } = useTranslation();
  const [searchParams] = useSearchParams();
  const clearCart = useCartStore((state) => state.clearCart);

  // Get order info from URL params if available
  const orderId = searchParams.get('order_id');
  const stockManaged = searchParams.has('id');
  const internalId = Number(searchParams.get('id'));
  const { data: order, isError } = useQuery({
    queryKey: ['payment-success', internalId],
    queryFn: () => api.get<ConfirmedOrder>(`/orders/${internalId}`),
    enabled: stockManaged && Number.isInteger(internalId) && internalId > 0,
    retry: false,
  });
  const confirmed = !stockManaged || order?.payment_status === 'paid';

  // Clear cart on successful payment
  useEffect(() => {
    if (stockManaged) {
      if (order?.payment_status !== 'paid') return;
      const pending = usePendingOrderStore.getState().pending;
      if (pending?.stockManaged && pending.orderId !== internalId) return;
      const cart = useCartStore.getState();
      if (cartMatchesOrder(cart.items, order.items)) clearCart();
      if (usePendingOrderStore.getState().pending?.orderId === internalId) usePendingOrderStore.getState().forgetOrder();
    } else clearCart();
    haptic.success();
  }, [clearCart, haptic, stockManaged, order, internalId]);

  const handleGoHome = () => {
    haptic.selection();
    navigate('/');
  };

  const handleViewOrders = () => {
    haptic.selection();
    navigate('/profile');
  };

  return (
    <div className="p-3 flex flex-col items-center justify-center min-h-[70vh] max-w-lg mx-auto">
      <motion.div
        initial={{ opacity: 0, scale: 0.8 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.5, type: 'spring', stiffness: 200 }}
        className="w-full"
      >
        <Card className="p-6 text-center">
          {/* Success Icon */}
          <motion.div
            initial={{ scale: 0 }}
            animate={{ scale: 1 }}
            transition={{ delay: 0.2, type: 'spring', stiffness: 300 }}
            className="neumorphic w-24 h-24 rounded-full flex items-center justify-center mx-auto mb-6"
            style={{ background: 'var(--primary)' }}
          >
            {confirmed ? <CheckCircle size={48} className="text-white" /> : <Clock size={48} className="text-white" />}
          </motion.div>

          {/* Success Message */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3 }}
          >
            <h1 className="text-2xl font-bold text-[var(--text-primary)] mb-2">
              {t(confirmed ? 'payment.success' : 'checkout.payment_processing')}
            </h1>
            <p className="text-[var(--text-secondary)] mb-2">
              {t(confirmed ? 'payment.successDescription' : isError ? 'checkout.payment_outcome_unknown' : 'checkout.payment_processing')}
            </p>
            {orderId && (
              <p className="text-[var(--text-muted)] text-sm mb-6">
                {t('orders.orderNumber')}: <span className="font-medium">{orderId}</span>
              </p>
            )}
            {order?.items.map((item) => <p key={item.id} className="text-sm">
              {item.product_name}{item.size ? ` (${item.size})` : ''} ×{item.quantity}
            </p>)}
          </motion.div>

          {/* Info Card */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.4 }}
            className="neumorphic-inset p-4 rounded-[var(--radius-md)] mb-6"
          >
            <p className="text-sm text-[var(--text-secondary)]">
              {t('payment.contactInfo')}
            </p>
          </motion.div>

          {/* Action Buttons */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.5 }}
            className="space-y-3"
          >
            <Button
              variant="primary"
              size="lg"
              fullWidth
              onClick={handleViewOrders}
            >
              <Package size={18} className="mr-2" />
              {t('profile.myOrders')}
            </Button>

            <Button
              variant="secondary"
              size="lg"
              fullWidth
              onClick={handleGoHome}
            >
              <Home size={18} className="mr-2" />
              {t('payment.backToHome')}
            </Button>
          </motion.div>
        </Card>
      </motion.div>
    </div>
  );
}
