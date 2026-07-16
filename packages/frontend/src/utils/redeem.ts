// ═══════════════════════════════════════════════════════════════════
// Biotact Club — правила частичного списания баллов (Этап 6)
// ═══════════════════════════════════════════════════════════════════
//
// ТОЧНОЕ зеркало бэкенда `packages/api/src/orders/service.py`
// (`max_redeemable` / `_validate_redeem_amount` / константы). Денежная логика продублирована ТОЛЬКО
// ради UX степпера — сервер остаётся авторитетным: невалидная сумма → 422 `invalid_redeem_amount`
// с `max_redeemable` в теле (бэкстоп). Держать в точной синхронизации с бэкендом.
//
// ⚠️ `subtotal` и `total` — ДВА раздельных параметра НАМЕРЕННО, не «раз доставка=0 они равны»: тот
// же дремлющий landmine на платную доставку, что закрыт комментарием в
// `packages/api/src/payments/service.py` (`build_ofd_items`). Баллами кроется только `subtotal`.

export const MIN_REDEEM = 500;
export const REDEEM_STEP = 100;
export const MIN_CARD_REMAINDER = 5000;

export interface OrderMoney {
  /** Текущий баланс баллов пользователя. */
  balance: number;
  /** Сумма товаров (то, что можно погасить баллами). */
  subtotal: number;
  /** Итог заказа (`subtotal` + доставка; сегодня доставка = 0, поэтому `total === subtotal`). */
  total: number;
}

/** Наибольшее кратное `REDEEM_STEP`, не превышающее `value` (округление вниз). */
function floorToStep(value: number): number {
  return Math.floor(value / REDEEM_STEP) * REDEEM_STEP;
}

/**
 * Наибольшая сумма баллов, применимая к заказу — `0`, если списание недоступно.
 *
 * Зеркало `orders/service.py:max_redeemable`: два кандидата, побеждает больший.
 *   • full-cover — потратить ровно `subtotal` (только если он сам кратен шагу) при достаточном
 *     балансе и остатке картой 0 либо ≥ `MIN_CARD_REMAINDER`;
 *   • partial — наибольшее кратное шагу, оставляющее картой ≥ `MIN_CARD_REMAINDER`, ограниченное
 *     балансом и `subtotal`.
 */
export function maxRedeemable({ balance, subtotal, total }: OrderMoney): number {
  const eligible = subtotal; // баллами кроется только товар, не доставка
  const candidates: number[] = [];

  if (balance >= eligible && eligible === floorToStep(eligible)) {
    const cardRemainder = total - eligible;
    if (cardRemainder === 0 || cardRemainder >= MIN_CARD_REMAINDER) {
      candidates.push(eligible);
    }
  }

  const partial = floorToStep(Math.min(balance, eligible, total - MIN_CARD_REMAINDER));
  if (partial >= MIN_REDEEM) {
    candidates.push(partial);
  }

  return candidates.length > 0 ? Math.max(...candidates) : 0;
}

/**
 * Привести желаемую сумму степпера к ВАЛИДНОЙ: `0` (только карта) или значение, кратное шагу, в
 * `[MIN_REDEEM, maxRedeemable]`, оставляющее картой 0 либо ≥ `MIN_CARD_REMAINDER`. Мёртвая зона
 * (остаток картой 1..`MIN_CARD_REMAINDER`-1) не возвращается никогда — перепрыгивается.
 *
 * Верх ползунка (при доступном full-cover) даёт полное покрытие; всё, что ниже мёртвой зоны,
 * снапится к partial-потолку; ниже `MIN_REDEEM` — к `0`.
 */
export function clampRedeem(desired: number, money: OrderMoney): number {
  const max = maxRedeemable(money);
  if (max <= 0 || desired <= 0) return 0;

  const partialMax = floorToStep(
    Math.min(money.balance, money.subtotal, money.total - MIN_CARD_REMAINDER),
  );

  // Full-cover доступен ⇔ он поднял потолок выше partial-предела. Тянущий до самого верха хочет
  // полное покрытие (остаток картой = доставка, сегодня 0).
  const fullCoverAvailable = max > partialMax;
  if (fullCoverAvailable && desired >= max) return max;

  const amount = Math.min(floorToStep(desired), partialMax);
  return amount >= MIN_REDEEM ? amount : 0;
}
