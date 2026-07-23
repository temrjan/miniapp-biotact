import { Card } from '@components/ui/Card';
import { useTranslation } from '@/i18n';
import { useReleaseReservations } from '@hooks/useReleaseReservations';

interface RedeemReserveNoticeProps {
  /** Сколько баллов удерживают собственные незавершённые заказы (баланс − доступное). */
  reservedPoints: number;
  /** Когда истекает самый ранний резерв; null — если срок неизвестен. */
  reservedUntil: Date | null;
  formatPrice: (price: number) => string;
}

/**
 * Объяснение, куда делись баллы: часть удерживает собственный незавершённый заказ (резерв Модели-1).
 *
 * Отдельный компонент, а не кусок чекаута: показывается он ИМЕННО тогда, когда степпера нет —
 * резерв съел всё доступное. Это главный случай прод-дефекта (Профиль показывает баллы, чекаут
 * говорит «недостаточно»), и он обязан быть покрыт тестом сам по себе.
 *
 * Формулировка намеренно «попробуйте снова после», а не «баллы вернутся в»: по таймеру не
 * происходит ничего — протухший резерв отменяет следующая попытка списания.
 */
export function RedeemReserveNotice({
  reservedPoints,
  reservedUntil,
  formatPrice,
}: RedeemReserveNoticeProps) {
  const { t } = useTranslation();
  const release = useReleaseReservations();
  if (reservedPoints <= 0) return null;

  return (
    <Card className="p-4 space-y-3" data-testid="redeem-reserve-notice">
      <p className="text-sm text-[var(--text-muted)]">
        {t('checkout.redeem.reserved', { points: formatPrice(reservedPoints) })}
        {reservedUntil &&
          ' ' +
            t('checkout.redeem.reservedUntil', {
              // Локаль задана явно — как и формат баланса в карточке лояльности. С `undefined`
              // формат берётся с устройства, и телефон в en-US выдал бы «02:30 PM» посреди
              // русского текста.
              time: reservedUntil.toLocaleTimeString('ru-RU', {
                hour: '2-digit',
                minute: '2-digit',
              }),
            })}
      </p>

      <button
        type="button"
        // disabled на время запроса — единственная защита от двойного нажатия: второй запрос
        // сервер отдал бы как «освобождено 0», и человек прочитал бы это как «не сработало».
        disabled={release.isPending}
        onClick={() => release.mutate()}
        className="text-sm font-medium text-[var(--primary)] disabled:opacity-50"
      >
        {release.isPending ? t('checkout.redeem.releasing') : t('checkout.redeem.release')}
      </button>

      {release.isError && (
        // Причину назвать НЕЛЬЗЯ: сервер сам не отличает «счёт уже оплачен» от «лёг транспорт»
        // (delete_invoice возвращает False в обоих случаях), поэтому текст нейтральный.
        <p className="text-sm text-[var(--error,#c0392b)]" data-testid="release-error">
          {t('checkout.redeem.releaseFailed')}
        </p>
      )}
    </Card>
  );
}
