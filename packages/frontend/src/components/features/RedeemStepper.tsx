import { Gem } from 'lucide-react';

import { Card } from '@components/ui/Card';
import { useTranslation } from '@/i18n';
import { REDEEM_STEP, clampRedeem, maxRedeemable, type OrderMoney } from '@/utils/redeem';

interface RedeemStepperProps {
  /** Полный баланс — участвует ТОЛЬКО в подписи; все расчёты идут по {@link available}. */
  balance: number;
  /**
   * Сколько можно потратить прямо сейчас = баланс минус резервы своих незавершённых заказов.
   * Ползунок считается отсюда, иначе он предложит сумму, которую сервер отобьёт 409-м.
   */
  available: number;
  /** Сумма товаров — то, что можно погасить баллами. */
  subtotal: number;
  /** Итог заказа (subtotal + доставка). Остаток картой = total − value. */
  total: number;
  /** Текущая выбранная сумма списания (контролируемая родителем). */
  value: number;
  onChange: (points: number) => void;
  formatPrice: (price: number) => string;
}

/**
 * Слайдер выбора баллов на шаге подтверждения: тянешь — «К оплате картой» режется вживую, кнопка
 * «Максимум». Валидность (шаг 100 / min 500 / перепрыг мёртвой зоны) держит {@link clampRedeem};
 * наружу невалидные значения не выходят. Сервер остаётся авторитетным (422 — бэкстоп).
 */
export function RedeemStepper({
  balance,
  available,
  subtotal,
  total,
  value,
  onChange,
  formatPrice,
}: RedeemStepperProps) {
  const { t } = useTranslation();
  const money: OrderMoney = { balance: available, subtotal, total };
  const max = maxRedeemable(money);
  const cardDue = total - value;
  // Часть баллов удерживает незавершённый заказ — тогда показанное число уже НЕ баланс, и называть
  // его балансом значит соврать в другую сторону (человек с 17 338 прочитал бы «Баланс: 38»).
  const hasReserve = available < balance;

  return (
    <Card className="p-4 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Gem size={18} className="text-[var(--primary)]" />
          <span className="font-semibold">{t('checkout.redeem.title')}</span>
        </div>
        <span className="text-sm text-[var(--text-muted)]">
          {hasReserve ? t('checkout.redeem.available') : t('checkout.redeem.balance')}:{' '}
          {formatPrice(available)}
        </span>
      </div>

      <div className="flex items-center justify-between">
        <span className="text-sm text-[var(--text-muted)]">{t('checkout.redeem.apply')}</span>
        <button
          type="button"
          onClick={() => onChange(max)}
          className="text-sm font-medium text-[var(--primary)]"
        >
          {t('checkout.redeem.max')}
        </button>
      </div>

      <input
        type="range"
        min={0}
        max={max}
        step={REDEEM_STEP}
        value={value}
        onChange={(event) => onChange(clampRedeem(Number(event.target.value), money))}
        aria-label={t('checkout.redeem.apply')}
        className="w-full accent-[var(--primary)]"
      />

      <div className="flex justify-between text-sm">
        <span className="text-[var(--text-muted)]">{t('checkout.redeem.applied')}</span>
        <span className="font-medium text-[var(--primary)]">−{formatPrice(value)}</span>
      </div>
      <div className="flex justify-between font-semibold">
        <span>{t('checkout.redeem.cardDue')}</span>
        <span>{formatPrice(cardDue)}</span>
      </div>
    </Card>
  );
}
