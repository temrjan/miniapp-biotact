import { useState, useCallback, useEffect, useRef } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import { Phone, MapPin, Truck, Store, Check, ChevronRight, Loader2, Pencil } from 'lucide-react';
import { Card } from '@components/ui/Card';
import { PhoneInput } from '@components/features/PhoneInput';
import { useBackButton } from '@hooks/useBackButton';
import { useMainButton } from '@hooks/useMainButton';
import { useHaptic, usePlatform } from '@hooks/useTelegram';
import { useTranslation } from '@/i18n';
import { useUserStore } from '@stores/userStore';
import { useCartItems, useCartTotalAmount } from '@stores/cartStore';
import { useLocationStore, getDisplayAddress } from '@stores/locationStore';
import { useLoyalty } from '@hooks/useLoyalty';
import { usePayment } from '@hooks/usePayment';
import { RedeemStepper } from '@components/features/RedeemStepper';
import { RedeemReserveNotice } from '@components/features/RedeemReserveNotice';
import type { OrderQuote } from '@/utils/order';
import type { CartItem } from '@/types';
import { clampRedeem, maxRedeemable } from '@/utils/redeem';
import { isLegalEnabled } from '@/legal/flag';
import { ROUTES } from '@utils/constants';

type CheckoutStep = 'phone' | 'delivery' | 'address' | 'confirm';
type DeliveryType = 'delivery' | 'pickup';

interface WebButtonProps {
  onClick: () => void;
  isActive: boolean;
  isProcessing: boolean;
  text: string;
  processingText: string;
}

function WebButton({ onClick, isActive, isProcessing, text, processingText }: WebButtonProps) {
  return (
    <motion.button
      whileTap={{ scale: 0.98 }}
      onClick={onClick}
      disabled={!isActive}
      className={`w-full h-12 bg-[#2a6b50] text-white font-semibold rounded-xl text-lg transition-opacity ${
        isActive ? '' : 'opacity-50 cursor-not-allowed'
      }`}
    >
      {isProcessing ? (
        <span className="flex items-center justify-center gap-2">
          <Loader2 size={20} className="animate-spin" />
          {processingText}
        </span>
      ) : (
        text
      )}
    </motion.button>
  );
}

export function CheckoutPage() {
  const navigate = useNavigate();
  const haptic = useHaptic();
  const platform = usePlatform();
  const { t } = useTranslation();
  const { profile, isLoading, fetchProfile, updatePhone } = useUserStore();
  const items = useCartItems();
  const cartTotal = useCartTotalAmount();
  const [quoted, setQuoted] = useState<{ items: CartItem[]; quote: OrderQuote } | null>(null);
  const quote = quoted?.items === items ? quoted.quote : null;
  const totalAmount = quote ? Number(quote.total) : cartTotal;
  const subtotal = quote ? Number(quote.subtotal) : cartTotal;
  const { address, openModal, isModalOpen } = useLocationStore();

  const [step, setStep] = useState<CheckoutStep>('phone');
  const [phone, setPhone] = useState('+998');
  const [deliveryType, setDeliveryType] = useState<DeliveryType>('delivery');
  const [isEditingPhone, setIsEditingPhone] = useState(false);
  const [pointsToApply, setPointsToApply] = useState(0);
  const phoneSyncedRef = useRef(false);
  const { data: loyalty } = useLoyalty();

  const isWebPlatform = platform === 'web' || platform === 'unknown';

  const formatPrice = useCallback(
    (price: number): string => {
      return price.toLocaleString('ru-RU', { maximumFractionDigits: 2 }) + ' ' + t('currency.sum');
    },
    [t]
  );

  useEffect(() => {
    void fetchProfile().then(() => {
      const currentProfile = useUserStore.getState().profile;
      if (currentProfile?.phone && !phoneSyncedRef.current) {
        phoneSyncedRef.current = true;
        setPhone(currentProfile.phone);
        if (currentProfile.phone.length >= 13) {
          setStep('delivery');
        }
      }
    });
  }, [fetchProfile]);

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

  // Biotact Club — points redemption (Stage 6). Membership + balance decide whether the stepper is
  // offered; the amount is authoritative server-side (422 backstop), redeem.ts mirrors the rules for
  // the UX. ``subtotal === total`` today because delivery is free — when a paid delivery lands they
  // become distinct (subtotal from the cart, total = subtotal + fee) and only ``subtotal`` is
  // redeemable (see redeem.ts / payments/service.py).
  const balance = loyalty?.balance ?? 0;
  // Списание считается от ДОСТУПНОГО, не от баланса: часть баллов может удерживать собственный
  // незавершённый заказ (резерв Модели-1), и предложить их — значит гарантированно получить 409.
  const available = loyalty?.available ?? 0;
  const reservedUntil = loyalty?.reservedUntil ?? null;
  const reservedPoints = Math.max(0, balance - available);
  const quoteAvailable = quote ? Math.min(available, quote.max_redeemable) : available;
  const money = { balance: quoteAvailable, subtotal, total: totalAmount };
  // Points redemption is safe since Model-1 (backend): a partial spend RESERVES points and the real
  // debit lands only when the card settles, so an abandoned payment can no longer burn points. (The
  // temporary kill-switch that hid this stepper after the burn incident is removed with that fix.)
  const isMember = loyalty?.isMember ?? false;
  const canRedeem = isMember && maxRedeemable(money) > 0;
  const redeemPoints = canRedeem ? pointsToApply : 0;
  const amountDue = totalAmount - redeemPoints;

  const deliveryAddress =
    deliveryType === 'delivery' && address ? getDisplayAddress(address) : null;
  const handleInvalidAmount = useCallback((max: number | null) => {
    if (max !== null) setPointsToApply(max);
  }, []);
  const handleQuote = useCallback(
    (freshQuote: OrderQuote) => {
      setQuoted({ items, quote: freshQuote });
      setPointsToApply((points) =>
        clampRedeem(points, {
          balance: freshQuote.max_redeemable,
          subtotal: Number(freshQuote.subtotal),
          total: Number(freshQuote.total),
        }) === points
          ? points
          : 0
      );
    },
    [items]
  );
  const { pay, isProcessing } = usePayment({
    items,
    deliveryAddress,
    name: profile?.first_name || null,
    phone,
    redeemPoints,
    displayedTotal: totalAmount,
    onQuote: handleQuote,
    onInvalidAmount: handleInvalidAmount,
  });

  const getMainButtonConfig = useCallback(() => {
    switch (step) {
      case 'phone':
        return {
          text: t('common.next'),
          onClick: handlePhoneContinue,
          isActive: phone.length >= 13,
        };
      case 'delivery':
        return {
          text:
            deliveryType === 'delivery' ? t('checkout.selectAddress') : t('checkout.placeOrder'),
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
      case 'confirm': {
        const payText =
          redeemPoints >= totalAmount
            ? t('checkout.redeem.payWithPoints')
            : t('checkout.payment') + ' ' + formatPrice(amountDue);
        return {
          text: isProcessing ? t('checkout.processing') : payText,
          onClick: pay,
          isActive: !isProcessing,
        };
      }
      default:
        return { text: '', onClick: () => {}, isActive: false };
    }
  }, [
    step,
    phone,
    deliveryType,
    address,
    totalAmount,
    amountDue,
    redeemPoints,
    isProcessing,
    haptic,
    pay,
    handlePhoneContinue,
    t,
    formatPrice,
  ]);

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

  const webButtonProps: WebButtonProps = {
    onClick: mainButtonConfig.onClick,
    isActive: mainButtonConfig.isActive,
    isProcessing,
    text: mainButtonConfig.text,
    processingText: t('checkout.processing'),
  };

  return (
    <div className="min-h-screen pb-24 md:pb-4">
      <div className="p-3 space-y-4 max-w-4xl mx-auto">
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
                    <p className="text-sm text-[var(--text-muted)]">
                      {t('checkout.phoneRequired')}
                    </p>
                  </div>
                </div>
                <PhoneInput value={phone} onChange={setPhone} autoFocus />
                {/* Web button inside card */}
                {isWebPlatform && (
                  <div className="mt-4 hidden md:block">
                    <WebButton {...webButtonProps} />
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
                    <p className="text-sm text-[var(--text-muted)]">
                      {t('checkout.deliveryCourier')}
                    </p>
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
                  <WebButton {...webButtonProps} />
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
                    <WebButton {...webButtonProps} />
                  </div>
                )}
              </Card>
            </motion.div>
          )}

          {step === 'confirm' && (
            <motion.div
              key="confirm"
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              className="space-y-3"
            >
              <h2 className="font-bold text-lg">{t('checkout.orderConfirmation')}</h2>

              {canRedeem && (
                <RedeemStepper
                  balance={balance}
                  available={available}
                  maxPoints={quote?.max_redeemable ?? maxRedeemable(money)}
                  subtotal={subtotal}
                  total={totalAmount}
                  value={pointsToApply}
                  onChange={setPointsToApply}
                  formatPrice={formatPrice}
                />
              )}

              {/* Рендерится ОТДЕЛЬНО от степпера и намеренно: главный случай — когда резерв съел
                  всё доступное, степпера нет вовсе, и без этой строки человек видит свои баллы в
                  Профиле и не понимает, почему их «нет». Молчать здесь — и есть исходный баг.
                  Гейт по членству — потому что плашка утверждает КОНКРЕТНУЮ причину («незавершённый
                  заказ»), а вне клуба списаний не бывает и утверждать это было бы неправдой. */}
              {isMember && (
                <RedeemReserveNotice
                  reservedPoints={reservedPoints}
                  reservedUntil={reservedUntil}
                  formatPrice={formatPrice}
                />
              )}

              <Card className="p-4">
                <div className="space-y-3">
                  <div className="flex justify-between">
                    <span className="text-[var(--text-muted)]">{t('checkout.itemsCount')}</span>
                    <span>
                      {items.length} {t('checkout.pcs')}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[var(--text-muted)]">{t('checkout.phone')}</span>
                    <span>{phone}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-[var(--text-muted)]">{t('checkout.method')}</span>
                    <span>
                      {deliveryType === 'delivery' ? t('checkout.delivery') : t('checkout.pickup')}
                    </span>
                  </div>
                  {deliveryType === 'delivery' && address && (
                    <div className="flex justify-between">
                      <span className="text-[var(--text-muted)]">{t('checkout.address')}</span>
                      <span className="text-right ml-4">{getDisplayAddress(address)}</span>
                    </div>
                  )}
                  <hr className="border-[var(--border)]" />
                  <div className="flex justify-between font-bold text-lg">
                    <span>{t('cart.total')}</span>
                    <span className="text-[var(--primary)]">{formatPrice(totalAmount)}</span>
                  </div>
                  {/* Акцепт Условий продажи (п. 3.1 Условий: доступ к тексту ДО оформления) —
                      строка живёт в видимом блоке итога: нативная MainButton вне DOM.
                      Только при включённом флаге VITE_LEGAL_DOCS. */}
                  {isLegalEnabled() && (
                    <p className="text-xs text-[var(--text-muted)] text-center">
                      {t('checkout.acceptTermsPrefix')}{' '}
                      <Link
                        to={`${ROUTES.LEGAL}/terms`}
                        className="text-[var(--primary)] underline"
                      >
                        {t('checkout.acceptTermsLink')}
                      </Link>
                    </p>
                  )}
                </div>

                {/* Web button INSIDE Card - same as CartPage */}
                {isWebPlatform && (
                  <div className="mt-4 hidden md:block">
                    <WebButton {...webButtonProps} />
                  </div>
                )}
              </Card>

              {isProcessing && (
                <Card className="p-4">
                  <div className="flex items-center justify-center gap-3">
                    <Loader2 size={24} className="animate-spin text-[var(--primary)]" />
                    <span className="text-[var(--text-muted)]">
                      {t('checkout.creatingPayment')}
                    </span>
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
          <WebButton {...webButtonProps} />
        </div>
      )}
    </div>
  );
}
