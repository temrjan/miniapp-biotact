import { create } from 'zustand';
import { devtools, persist } from 'zustand/middleware';
import { STORAGE_KEYS } from '@/utils/constants';

// ═══════════════════════════════════════════════════════════════════
// Памятка «жду подтверждения оплаты заказа N»
// ═══════════════════════════════════════════════════════════════════
//
// При оплате картой Telegram уводит человека из мини-аппа в браузер (`WebApp.openLink` в
// `usePayment`), и экран «оплата прошла» открывается ТАМ — вместе со своей очисткой корзины.
// Корзина мини-аппа при этом остаётся полной. Памятка — то, по чему приложение узнаёт, за какой
// заказ человек ушёл платить, чтобы на возврате спросить сервер и очистить корзину у себя
// (`useClearCartOnPaidOrder`).
//
// Хранится в localStorage НАМЕРЕННО: обосновывающий случай — «закрыл браузер, вернулся через час»,
// когда мини-апп был выгружен и в памяти не осталось ничего.

/**
 * Сколько памятка живёт, прежде чем исчезнуть сама.
 *
 * Нужен собственный срок, потому что сервер про карточный заказ «не оплачен» не скажет никогда:
 * `expire_stale_partials` отменяет только заказы с баллами (`points_applied > 0`), а чисто
 * карточный брошенный заказ висит `unpaid` бессрочно. Без срока памятка передумавшего человека
 * дёргала бы сервер при каждом старте вечно.
 *
 * Сутки — покрывают «закрыл браузер, вернулся утром»; дальше тихая очистка корзины была бы уже
 * сюрпризом: человек мог собрать новую.
 */
export const PENDING_ORDER_TTL_MS = 24 * 60 * 60 * 1000;

export interface PendingOrder {
  /**
   * ВНУТРЕННИЙ ЧИСЛОВОЙ id заказа — именно им ходит `GET /orders/{order_id}` (принимает `int`).
   *
   * НЕ `order_number`: тот выглядит как `ORD-20260806123456-A1B2C3`, уходит в адрес возврата и в
   * навигацию, и переиспользовать его здесь — значит получить 422/404 на каждой сверке и никогда
   * не очистить корзину, при внешне правильном коде.
   */
  orderId: number;
  /** Момент ухода на оплату, epoch ms — от него считается срок жизни памятки. */
  startedAt: number;
}

interface PendingOrderState {
  pending: PendingOrder | null;

  rememberOrder: (orderId: number) => void;
  forgetOrder: () => void;
}

export const usePendingOrderStore = create<PendingOrderState>()(
  devtools(
    persist(
      (set) => ({
        pending: null,

        rememberOrder: (orderId) => {
          set({ pending: { orderId, startedAt: Date.now() } }, false, 'rememberOrder');
        },

        forgetOrder: () => {
          set({ pending: null }, false, 'forgetOrder');
        },
      }),
      {
        name: STORAGE_KEYS.PENDING_ORDER,
      }
    ),
    { name: 'PendingOrderStore' }
  )
);

/** Памятка просрочена — сверять её больше не нужно, снимаем без запроса к серверу. */
export function isPendingOrderExpired(pending: PendingOrder, now: number = Date.now()): boolean {
  return now - pending.startedAt > PENDING_ORDER_TTL_MS;
}
