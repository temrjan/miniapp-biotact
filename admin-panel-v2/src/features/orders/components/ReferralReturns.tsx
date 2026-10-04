import { useRef, useState } from 'react';
import { isAxiosError } from 'axios';
import { Button } from '@/shared/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/shared/components/ui/card';
import { intentSchema, normalizeLines, referralReturnsApi } from '../api/referralReturnsApi';
import type { ReturnIntent } from '../api/referralReturnsApi';
import { useReferralReturns } from '../hooks/useReferralReturns';
import type { OrderItem } from '../types/order.types';

const confirmation = 'Подтверждаю уже выполненный возврат этих единиц. Запись уменьшит реферальный результат';
const storageKey = (orderId: string) => `biotact-referral-return:${orderId}`;
const statusOf = (error: unknown) => isAxiosError(error) ? error.response?.status : undefined;

const readIntent = (orderId: string): { intent: ReturnIntent | null; error: string } => {
  try {
    const saved = sessionStorage.getItem(storageKey(orderId));
    return { intent: saved ? intentSchema.parse(JSON.parse(saved)) : null, error: '' };
  } catch {
    return { intent: null, error: 'Не удалось восстановить подтверждение. Проверьте доступ к хранилищу и перезагрузите страницу.' };
  }
};

export const ReferralReturns = ({ orderId, items }: { orderId: string; items: OrderItem[] }) => {
  const summary = useReferralReturns(orderId);
  const [restored] = useState(() => readIntent(orderId));
  const [intent, setIntent] = useState<ReturnIntent | null>(restored.intent);
  const [quantities, setQuantities] = useState<Record<string, string>>({});
  const [message, setMessage] = useState(restored.error);
  const [pending, setPending] = useState(false);
  const [forbidden, setForbidden] = useState(false);
  const sending = useRef(false);

  const submit = async () => {
    if (sending.current || restored.error || forbidden || !summary.data || summary.error) return;
    let next = intent;
    if (!next) {
      const selected = summary.data.lines.flatMap(line => {
        const value = quantities[line.order_item_id] ?? '';
        return value === '' || value === '0' ? [] : [{ order_item_id: line.order_item_id, quantity: value }];
      });
      const parsed = intentSchema.safeParse({ return_id: crypto.randomUUID(), lines: normalizeLines(selected) });
      if (!parsed.success || parsed.data.lines.some(line => {
        const source = summary.data.lines.find(value => value.order_item_id === line.order_item_id);
        return !source || BigInt(line.quantity) > BigInt(source.paid_quantity) - BigInt(source.returned_quantity);
      })) {
        setMessage('Укажите положительное количество в пределах оставшегося.');
        return;
      }
      if (!window.confirm(confirmation)) return;
      next = parsed.data;
    }
    try {
      const raw = JSON.stringify(next);
      sessionStorage.setItem(storageKey(orderId), raw);
      if (sessionStorage.getItem(storageKey(orderId)) !== raw) throw new Error('Storage unavailable');
    } catch {
      setMessage('Не удалось сохранить подтверждение. Разрешите хранилище и повторите.');
      return;
    }
    setIntent(next);
    sending.current = true;
    setPending(true);
    setMessage('');
    try {
      await referralReturnsApi.confirm(orderId, next);
      sessionStorage.removeItem(storageKey(orderId));
      setIntent(null);
      setQuantities({});
      setMessage('Возврат подтверждён.');
      await summary.refetch();
    } catch (error) {
      const status = statusOf(error);
      if (status === 401 || status === 403) {
        setForbidden(true);
        setQuantities({});
        setMessage('Нет доступа к подтверждениям.');
      } else if (status === 409 || status === 422) {
        try {
          sessionStorage.removeItem(storageKey(orderId));
          setIntent(null);
          setQuantities({});
          setMessage('Подтверждение отклонено. Проверьте актуальные количества перед новым действием.');
        } catch {
          setMessage('Подтверждение отклонено. Не удалось очистить сохранённое действие; проверьте хранилище.');
        }
        await summary.refetch();
      } else {
        setMessage('Результат отправки неизвестен. Повторите сохранённое подтверждение с теми же количествами.');
      }
    } finally {
      sending.current = false;
      setPending(false);
    }
  };

  const status = statusOf(summary.error);
  if (status === 404) return null;
  if (forbidden || status === 401 || status === 403) return <p role="alert">Нет доступа к подтверждениям.</p>;
  return <Card>
    <CardHeader><CardTitle>Подтверждённый возврат товара кампании</CardTitle></CardHeader>
    <CardContent className="space-y-3">
      {summary.isPending ? <p>Загрузка подтверждённых количеств…</p> : summary.error ?
        <><p role="alert">Не удалось загрузить количества.</p><Button onClick={() => void summary.refetch()}>Повторить загрузку</Button></> :
        summary.data && <>
          {summary.data.lines.map(line => {
            const remaining = (BigInt(line.paid_quantity) - BigInt(line.returned_quantity)).toString();
            const item = items.find(value => Number.isSafeInteger(value.id) && String(value.id) === line.order_item_id);
            return <div key={line.order_item_id}>
              <p>{item?.product_name ?? `Строка ${line.order_item_id}`}: оплачено {line.paid_quantity}, возвращено {line.returned_quantity}, осталось {remaining}</p>
              <label>Количество возврата строки {line.order_item_id}
                <input inputMode="numeric" aria-label={`Количество ${line.order_item_id}`} value={intent?.lines.find(value => value.order_item_id === line.order_item_id)?.quantity ?? quantities[line.order_item_id] ?? ''}
                  disabled={pending || !!intent || remaining === '0'}
                  onChange={event => setQuantities(previous => ({ ...previous, [line.order_item_id]: event.target.value }))} />
              </label>
            </div>;
          })}
          <Button variant="outline" disabled={pending || !!intent || !!restored.error} onClick={() => setQuantities(Object.fromEntries(summary.data.lines.map(line => [line.order_item_id, (BigInt(line.paid_quantity) - BigInt(line.returned_quantity)).toString()])))}>Все оставшиеся</Button>
          <p>{confirmation}</p>
          <Button disabled={pending || !!restored.error} onClick={() => void submit()}>{pending ? 'Отправка…' : intent ? 'Повторить сохранённое подтверждение' : 'Подтвердить возврат'}</Button>
        </>}
      {message && <p role="status">{message}</p>}
    </CardContent>
  </Card>;
};
