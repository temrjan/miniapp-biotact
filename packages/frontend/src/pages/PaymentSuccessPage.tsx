import { useEffect } from 'react';
import { motion } from 'framer-motion';
import { CheckCircle, Home, Package } from 'lucide-react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Card } from '@components/ui/Card';
import { Button } from '@components/ui/Button';
import { useHaptic } from '@hooks/useTelegram';
import { useCartStore } from '@stores/cartStore';
import { useTranslation } from '@/i18n';

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

  // Clear cart on successful payment
  useEffect(() => {
    clearCart();
    haptic.success();
  }, [clearCart, haptic]);

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
            style={{ background: 'linear-gradient(135deg, #22c55e 0%, #16a34a 100%)' }}
          >
            <CheckCircle size={48} className="text-white" />
          </motion.div>

          {/* Success Message */}
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3 }}
          >
            <h1 className="text-2xl font-bold text-[var(--text-primary)] mb-2">
              {t('payment.success')}
            </h1>
            <p className="text-[var(--text-secondary)] mb-2">
              {t('payment.successDescription')}
            </p>
            {orderId && (
              <p className="text-[var(--text-muted)] text-sm mb-6">
                {t('orders.orderNumber')}: <span className="font-medium">{orderId}</span>
              </p>
            )}
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
