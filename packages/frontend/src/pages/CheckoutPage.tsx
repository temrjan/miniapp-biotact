import { useState, useCallback, useEffect } from 'react';
import WebApp from '@twa-dev/sdk';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { Phone, MapPin, Truck, Store, Check, ChevronRight, Loader2, Pencil } from 'lucide-react';
import { Card } from '@components/ui/Card';
import { PhoneInput } from '@components/features/PhoneInput';
import { useBackButton } from '@hooks/useBackButton';
import { useMainButton } from '@hooks/useMainButton';
import { useHaptic, usePlatform } from '@hooks/useTelegram';
import { useTranslation } from '@/i18n';
import { api } from '@/api/client';
import { useUserStore } from '@stores/userStore';
import { useCartItems, useCartTotalAmount } from '@stores/cartStore';
import { useLocationStore, getDisplayAddress } from '@stores/locationStore';

type CheckoutStep = 'phone' | 'delivery' | 'address' | 'confirm';
type DeliveryType = 'delivery' | 'pickup';

interface OrderItem {
  product_id: number;
  product_name: string;
  product_price: number;
  quantity: number;
  total: number;
}

interface CreateOrderRequest {
  subtotal: number;
  delivery_fee: number;
  total: number;
  delivery_address: string | null;
  delivery_phone: string | null;
  delivery_name: string | null;
  customer_note: string | null;
  items: OrderItem[];
}

interface OrderResponse {
  id: number;
  order_number: string;
  total: number;
}

interface PaymentResponse {
  payment_url: string;
  payment_uuid: string;
  order_id: number;
}

export function CheckoutPage() {
  const navigate = useNavigate();
  const haptic = useHaptic();
  const platform = usePlatform();
  const { t } = useTranslation();
  const { profile, isLoading, fetchProfile, updatePhone } = useUserStore();
  const items = useCartItems();
  const totalAmount = useCartTotalAmount();
  const { address, openModal, isModalOpen } = useLocationStore();

  const [step, setStep] = useState<CheckoutStep>('phone');
  const [phone, setPhone] = useState('+998');
  const [deliveryType, setDeliveryType] = useState<DeliveryType>('delivery');
  const [isProcessing, setIsProcessing] = useState(false);
  const [isEditingPhone, setIsEditingPhone] = useState(false);

  const isWebPlatform = platform === 'web' || platform === 'unknown';

  const formatPrice = useCallback(
    (price: number): string => {
      return Math.floor(price).toLocaleString('ru-RU').replace(/,/g, ' ') + ' ' + t('currency.sum');
    },
    [t]
  );

  useEffect(() => {
    void fetchProfile();
  }, [fetchProfile]);

  useEffect(() => {
    if (profile?.phone) {
      setPhone(profile.phone);
      if (!isEditingPhone && profile.phone.length >= 13) {
        setStep('delivery');
      }
    }
  }, [profile?.phone, isEditingPhone]);

  const handleBack = useCallback(() => {
    if (step === 'phone') {
      if (profile?.phone && !isEditingPhone) {
        setStep('delivery');
      } else {
        navigate(-1);
      }
    } else if (step === 'delivery') {
      setIsEditingPhone(true);
      setStep('phone');
    } else if (step === 'address') {
      setStep('delivery');
    } else if (step === 'confirm') {
      setStep(deliveryType === 'delivery' ? 'address' : 'delivery');
    }
  }, [step, navigate, deliveryType, profile?.phone, isEditingPhone]);

  useBackButton({ onClick: handleBack });

  const handlePhoneContinue = useCallback(async () => {
    if (phone.length < 13) return;
    haptic.medium();
    if (phone !== profile?.phone) {
      try {
        await updatePhone(phone);
      } catch (error) {
        console.error('Failed to save phone:', error);
      }
    }
    setIsEditingPhone(false);
    setStep('delivery');
  }, [phone, profile?.phone, updatePhone, haptic]);

  const handlePayment = useCallback(async () => {
    setIsProcessing(true);
    haptic.success();
    try {
      const orderItems: OrderItem[] = items.map((item) => ({
        product_id: parseInt(item.productId, 10),
        product_name: item.product.name,
        product_price: item.product.price,
        quantity: item.quantity,
        total: item.product.price * item.quantity,
      }));
      const deliveryAddress = deliveryType === 'delivery' && address ? getDisplayAddress(address) : null;
      const orderRequest: CreateOrderRequest = {
        subtotal: totalAmount,
        delivery_fee: 0,
        total: totalAmount,
        delivery_address: deliveryAddress,
        delivery_phone: phone,
        delivery_name: profile?.first_name || null,
        customer_note: null,
        items: orderItems,
      };
      const order = await api.post<OrderResponse>('/orders', orderRequest);
      const returnUrl = window.location.origin + '/payment/success?order_id=' + order.order_number;
      const payment = await api.post<PaymentResponse>('/payments/create', { order_id: order.id, return_url: returnUrl });
      WebApp.openLink(payment.payment_url);
    } catch (error) {
      console.error('Payment error:', error);
      haptic.error();
      alert(error instanceof Error ? error.message : t('common.error'));
      setIsProcessing(false);
    }
  }, [items, deliveryType, address, totalAmount, phone, profile, haptic, t]);

  const getMainButtonConfig = useCallback(() => {
    switch (step) {
      case 'phone':
        return { text: t('common.next'), onClick: handlePhoneContinue, isActive: phone.length >= 13 };
      case 'delivery':
        return {
          text: deliveryType === 'delivery' ? t('checkout.selectAddress') : t('checkout.placeOrder'),
          onClick: () => {
            haptic.medium();
            setStep(deliveryType === 'delivery' ? 'address' : 'confirm');
          },
          isActive: true,
        };
      case 'address':
        return {
          text: t('checkout.confirmAddress'),
          onClick: () => {
            haptic.medium();
            setStep('confirm');
          },
          isActive: !!address?.street && !!address?.house,
        };
      case 'confirm':
        return {
          text: isProcessing ? t('checkout.processing') : t('checkout.payment') + ' ' + formatPrice(totalAmount),
          onClick: handlePayment,
          isActive: !isProcessing,
        };
      default:
        return { text: '', onClick: () => {}, isActive: false };
    }
  }, [step, phone, deliveryType, address, totalAmount, isProcessing, haptic, handlePayment, handlePhoneContinue, t, formatPrice]);

  const mainButtonConfig = getMainButtonConfig();

  useMainButton({
    color: '#2a6b50',
    textColor: '#ffffff',
    text: mainButtonConfig.text,
    onClick: mainButtonConfig.onClick,
    isVisible: !isModalOpen && !isWebPlatform,
    isActive: mainButtonConfig.isActive,
    isProgressVisible: isLoading || isProcessing,
  });

  if (items.length === 0) {
    navigate('/cart');
    return null;
  }

  const stepIndex = ['phone', 'delivery', 'address', 'confirm'].indexOf(step);

  // Кнопка для web
  const WebButton = () => (
    <motion.button
      whileTap={{ scale: 0.98 }}
      onClick={mainButtonConfig.onClick}
      disabled={!mainButtonConfig.isActive}
      className={`w-full h-12 bg-[#2a6b50] text-white font-semibold rounded-xl text-lg transition-opacity ${
        mainButtonConfig.isActive ? '' : 'opacity-50 cursor-not-allowed'
      }`}
    >
      {isProcessing ? (
        <span className="flex items-center justify-center gap-2">
          <Loader2 size={20} className="animate-spin" />
          {t('checkout.processing')}
        </span>
      ) : (
        mainButtonConfig.text
      )}
    </motion.button>
  );

  return (
    <div className="min-h-screen pb-24 md:pb-4">
      <div className="p-4 space-y-4 max-w-7xl mx-auto">
        {/* Progress dots */}
        <div className="flex items-center justify-center gap-2 mb-4">
          {(['phone', 'delivery', 'address', 'confirm'] as CheckoutStep[]).map((s, i) => (
            <div
              key={s}
              className={`w-2 h-2 rounded-full transition-colors ${
                step === s
                  ? 'bg-[var(--primary)]'
                  : i < stepIndex
                  ? 'bg-[var(--primary)] opacity-50'
                  : 'bg-[var(--text-muted)] opacity-30'
              }`}
            />
          ))}
        </div>

        <AnimatePresence mode="wait">
          {step === 'phone' && (
            <motion.div
              key="phone"
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
            >
              <Card className="p-4">
                <div className="flex items-center gap-3 mb-4">
                  <div className="neumorphic w-12 h-12 rounded-full flex items-center justify-center">
                    <Phone size={24} className="text-[var(--primary)]" />
                  </div>
                  <div>
                    <h2 className="font-bold">{t('checkout.phone')}</h2>
                    <p className="text-sm text-[var(--text-muted)]">{t('checkout.phoneRequired')}</p>
                  </div>
                </div>
                <PhoneInput value={phone} onChange={setPhone} autoFocus />
                {/* Web button inside card */}
                {isWebPlatform && (
                  <div className="mt-4 hidden md:block">
                    <WebButton />
                  </div>
                )}
              </Card>
            </motion.div>
          )}

          {step === 'delivery' && (
            <motion.div
              key="delivery"
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              className="space-y-3"
            >
              <h2 className="font-bold text-lg">{t('checkout.paymentMethod')}</h2>

              <Card className="p-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Phone size={18} className="text-[var(--text-muted)]" />
                    <span>{phone}</span>
                  </div>
                  <button
                    onClick={() => {
                      haptic.light();
                      setIsEditingPhone(true);
                      setStep('phone');
                    }}
                    className="p-2 rounded-full hover:bg-[var(--bg-secondary)] transition-colors"
                  >
                    <Pencil size={16} className="text-[var(--text-muted)]" />
                  </button>
                </div>
              </Card>

              <Card
                className={`p-4 cursor-pointer transition-all ${
                  deliveryType === 'delivery' ? 'ring-2 ring-[var(--primary)]' : ''
                }`}
                onClick={() => {
                  haptic.selection();
                  setDeliveryType('delivery');
                }}
              >
                <div className="flex items-center gap-3">
                  <div
                    className={`w-12 h-12 rounded-full flex items-center justify-center ${
                      deliveryType === 'delivery' ? 'bg-[var(--primary)]' : 'neumorphic'
                    }`}
                  >
                    <Truck size={24} className={deliveryType === 'delivery' ? 'text-white' : ''} />
                  </div>
                  <div className="flex-1">
                    <h3 className="font-semibold">{t('checkout.delivery')}</h3>
                    <p className="text-sm text-[var(--text-muted)]">{t('checkout.deliveryCourier')}</p>
                  </div>
                  {deliveryType === 'delivery' && (
                    <Check size={24} className="text-[var(--primary)]" />
                  )}
                </div>
              </Card>

              <Card
                className={`p-4 cursor-pointer transition-all ${
                  deliveryType === 'pickup' ? 'ring-2 ring-[var(--primary)]' : ''
                }`}
                onClick={() => {
                  haptic.selection();
                  setDeliveryType('pickup');
                }}
              >
                <div className="flex items-center gap-3">
                  <div
                    className={`w-12 h-12 rounded-full flex items-center justify-center ${
                      deliveryType === 'pickup' ? 'bg-[var(--primary)]' : 'neumorphic'
                    }`}
                  >
                    <Store size={24} className={deliveryType === 'pickup' ? 'text-white' : ''} />
                  </div>
                  <div className="flex-1">
                    <h3 className="font-semibold">{t('checkout.pickup')}</h3>
                    <p className="text-sm text-[var(--text-muted)]">{t('checkout.pickupStore')}</p>
                  </div>
                  {deliveryType === 'pickup' && (
                    <Check size={24} className="text-[var(--primary)]" />
                  )}
                </div>
              </Card>

              {/* Web button */}
              {isWebPlatform && (
                <div className="hidden md:block pt-2">
                  <WebButton />
                </div>
              )}
            </motion.div>
          )}

          {step === 'address' && (
            <motion.div
              key="address"
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
            >
              <Card className="p-4">
                <div className="flex items-center gap-3 mb-4">
                  <div className="neumorphic w-12 h-12 rounded-full flex items-center justify-center">
                    <MapPin size={24} className="text-[var(--primary)]" />
                  </div>
                  <div>
                    <h2 className="font-bold">{t('checkout.deliveryAddress')}</h2>
                    <p className="text-sm text-[var(--text-muted)]">{t('checkout.addressHint')}</p>
                  </div>
                </div>
                <div
                  className="p-3 rounded-lg bg-[var(--bg-secondary)] cursor-pointer"
                  onClick={() => {
                    haptic.light();
                    openModal();
                  }}
                >
                  <div className="flex items-center justify-between">
                    <span className={address?.street ? '' : 'text-[var(--text-muted)]'}>
                      {getDisplayAddress(address)}
                    </span>
                    <ChevronRight size={20} className="text-[var(--text-muted)]" />
                  </div>
                </div>
                {/* Web button inside card */}
                {isWebPlatform && (
                  <div className="mt-4 hidden md:block">
                    <WebButton />
                  </div>
                )}
              </Card>
            </motion.div>
          )}

          {step === 'confirm' && (
            <motion.div
              key="confirm"
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, x: -20 }}
              className="space-y-3"
            >
              <h2 className="font-bold text-lg">{t('checkout.orderConfirmation')}</h2>

              <Card className="p-4">
                <div className="space-y-3">
                  <div className="flex justify-between">
                    <span className="text-[var(--text-muted)]">{t('checkout.itemsCount')}</span>
                    <span>{items.length} {t('checkout.pcs')}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[var(--text-muted)]">{t('checkout.phone')}</span>
                    <span>{phone}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[var(--text-muted)]">{t('checkout.method')}</span>
                    <span>{deliveryType === 'delivery' ? t('checkout.delivery') : t('checkout.pickup')}</span>
                  </div>
                  {deliveryType === 'delivery' && address && (
                    <div className="flex justify-between">
                      <span className="text-[var(--text-muted)]">{t('checkout.address')}</span>
                      <span className="text-right max-w-[200px]">{getDisplayAddress(address)}</span>
                    </div>
                  )}
                  <hr className="border-[var(--border)]" />
                  <div className="flex justify-between font-bold text-lg">
                    <span>{t('cart.total')}</span>
                    <span className="text-[var(--primary)]">{formatPrice(totalAmount)}</span>
                  </div>
                </div>

                {/* Web button INSIDE Card - same as CartPage */}
                {isWebPlatform && (
                  <div className="mt-4 hidden md:block">
                    <WebButton />
                  </div>
                )}
              </Card>

              {isProcessing && (
                <Card className="p-4">
                  <div className="flex items-center justify-center gap-3">
                    <Loader2 size={24} className="animate-spin text-[var(--primary)]" />
                    <span className="text-[var(--text-muted)]">{t('checkout.creatingPayment')}</span>
                  </div>
                </Card>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Mobile Web Button - fixed at bottom, only on mobile */}
      {isWebPlatform && !isModalOpen && (
        <div className="fixed bottom-0 left-0 right-0 p-4 bg-[var(--bg-primary)] border-t border-[var(--border)] md:hidden safe-area-bottom">
          <WebButton />
        </div>
      )}
    </div>
  );
}
