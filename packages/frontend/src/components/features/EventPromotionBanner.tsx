import { useTranslation } from '@/i18n';
import { useEventPromotion } from '@hooks/useEventPromotion';
import { lastPromotionDay } from '@/utils/eventPromotion';

/**
 * States the buyer's event discount on every MainLayout page (event-qr-discount R-50, D7). It names
 * no price: catalogue and product pages keep ordinary prices, the discount appears in the cart.
 */
export function EventPromotionBanner() {
  const { t } = useTranslation();
  const { data: promotion } = useEventPromotion();
  if (!promotion) return null;
  return (
    <div
      role="status"
      className="flex-shrink-0 bg-[var(--primary)]/10 px-4 py-2 text-center text-sm font-medium text-[var(--primary)]"
    >
      {t('eventPromotion.banner', {
        title: promotion.title,
        p: promotion.percent,
        date: lastPromotionDay(promotion.endsAt),
      })}
    </div>
  );
}
