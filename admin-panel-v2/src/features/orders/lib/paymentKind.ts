import type { Order } from "../types/order.types"

/**
 * Чем на самом деле оплачен заказ.
 *
 * `payment_status` отвечает только на вопрос «закрыт ли заказ», но не на вопрос «пришли ли деньги».
 * С появлением списания баллов (Этап 6) заказ может быть `paid`, при этом на счёт не поступило
 * ни одного сума — он покрыт баллами целиком. Для сверки поступлений это разные события, и
 * админка обязана их различать.
 */
export type PaymentKind = "card" | "partial-points" | "full-points"

/**
 * Определяет вид оплаты по заказу.
 *
 * `total` в API админки приходит СТРОКОЙ (`order.types.ts`), поэтому приводится явно: сравнение
 * числа со строкой в JS сработало бы за счёт неявного приведения, но достаточно кому-то заменить
 * `>=` на `===` или сложить значения — и денежная семантика поедет молча.
 *
 * Граница `>=`, а не `>`: покрытие ровно на всю сумму — это ПОЛНОЕ покрытие, карта не участвует.
 */
export function getPaymentKind(order: Pick<Order, "points_applied" | "total">): PaymentKind {
  const points = order.points_applied ?? 0
  if (points <= 0) return "card"
  return points >= Number(order.total) ? "full-points" : "partial-points"
}

/** Сколько собрала карта: остаток после баллов, никогда не отрицательный. */
export function getCardAmount(order: Pick<Order, "points_applied" | "total">): number {
  return Math.max(0, Number(order.total) - (order.points_applied ?? 0))
}
