import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AxiosError } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ReferralReturns } from './ReferralReturns';
import { referralReturnsApi } from '../api/referralReturnsApi';
import type { ReturnIntent, ReturnSummary } from '../api/referralReturnsApi';

const transport = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock('@/shared/api/client', () => ({ apiClient: transport }));
const key = 'biotact-referral-return:100';
const summary: ReturnSummary = { order_id: '100', order_version: '1', lines: [
  { order_item_id: '4201', product_id: '39', paid_quantity: '2', returned_quantity: '0' },
  { order_item_id: '4202', product_id: '39', paid_quantity: '1', returned_quantity: '0' },
] };
function response(value: ReturnIntent) {
  return { data: { receipt: { ...value, order_id: '100', order_version: '2', confirmed_at: '2026-10-05T00:00:00.000000Z' }, replayed: false } };
}
function httpError(status: number) {
  const error = new AxiosError('Boundary refusal');
  Object.defineProperty(error, 'response', { value: { status } });
  return error;
}
function mount() {
  const query = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(<QueryClientProvider client={query}><ReferralReturns orderId="100" items={[
    { id: 4201, product_id: 39, product_name: 'Первая строка', quantity: 2, product_price: '100', total: '200' },
    { id: 4202, product_id: 39, product_name: 'Вторая строка', quantity: 1, product_price: '100', total: '100' },
  ]} /></QueryClientProvider>);
}
async function ready() { await screen.findByLabelText('Количество 4201'); }

describe('Retained quantity confirmations', () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.restoreAllMocks();
    transport.get.mockReset().mockResolvedValue({ data: summary });
    transport.post.mockReset().mockImplementation((_url: string, value: ReturnIntent) => Promise.resolve(response(value)));
    vi.spyOn(window, 'confirm').mockReturnValue(true);
  });
  afterEach(cleanup);

  it('confirms partial and remaining units by item ID and requires explicit consent', async () => {
    const partial = { ...summary, order_version: '2', lines: summary.lines.map(line => ({ ...line, returned_quantity: line.order_item_id === '4201' ? '1' : '0' })) };
    const full = { ...summary, order_version: '3', lines: summary.lines.map(line => ({ ...line, returned_quantity: line.paid_quantity })) };
    transport.get.mockResolvedValueOnce({ data: summary }).mockResolvedValueOnce({ data: partial }).mockResolvedValue({ data: full });
    mount(); await ready();
    expect(screen.getByText(/Первая строка: оплачено 2, возвращено 0, осталось 2/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Количество 4201'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить возврат' }));
    await screen.findByText('Возврат подтверждён.');
    expect(window.confirm).toHaveBeenCalledWith('Подтверждаю уже выполненный возврат этих единиц. Запись уменьшит реферальный результат');
    expect(transport.post.mock.calls[0]?.[1].lines).toEqual([{ order_item_id: '4201', quantity: '1' }]);
    expect(sessionStorage.getItem(key)).toBeNull();
    await screen.findByText(/Первая строка: оплачено 2, возвращено 1, осталось 1/);
    fireEvent.click(screen.getByRole('button', { name: 'Все оставшиеся' }));
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить возврат' }));
    await waitFor(() => expect(transport.post).toHaveBeenCalledTimes(2));
    expect(transport.post.mock.calls[1]?.[1].lines).toEqual([{ order_item_id: '4201', quantity: '1' }, { order_item_id: '4202', quantity: '1' }]);
    await screen.findByText(/Первая строка: оплачено 2, возвращено 2, осталось 0/);
    expect(transport.post.mock.calls[1]?.[1].return_id).not.toBe(transport.post.mock.calls[0]?.[1].return_id);
  });

  it('persists before send, prevents duplicate clicks and retries unknown outcome after reload', async () => {
    let release: ((value: ReturnType<typeof response>) => void) | undefined;
    transport.post.mockImplementationOnce((_url: string, intent: ReturnIntent) => {
      expect(JSON.parse(sessionStorage.getItem(key) ?? '{}')).toEqual(intent);
      return new Promise<ReturnType<typeof response>>(resolve => { release = resolve; });
    });
    const view = mount(); await ready();
    fireEvent.change(screen.getByLabelText('Количество 4201'), { target: { value: '1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить возврат' }));
    fireEvent.click(screen.getByRole('button', { name: 'Отправка…' }));
    expect(transport.post).toHaveBeenCalledTimes(1);
    const saved = JSON.parse(sessionStorage.getItem(key) ?? '{}') as ReturnIntent;
    view.unmount();
    mount(); await ready();
    expect(screen.getByLabelText('Количество 4201')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Повторить сохранённое подтверждение' }));
    await screen.findByText('Возврат подтверждён.');
    expect(transport.post.mock.calls[1]?.[1]).toEqual(saved);
    expect(window.confirm).toHaveBeenCalledTimes(1);
    release?.(response(saved));
  });

  it('keeps exact intent on 503, clears it only after definite refusal', async () => {
    transport.post.mockRejectedValueOnce(httpError(503)).mockRejectedValueOnce(httpError(409));
    mount(); await ready();
    fireEvent.click(screen.getByRole('button', { name: 'Все оставшиеся' }));
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить возврат' }));
    await screen.findByText(/Результат отправки неизвестен/);
    const saved = JSON.parse(sessionStorage.getItem(key) ?? '{}');
    fireEvent.click(screen.getByRole('button', { name: 'Повторить сохранённое подтверждение' }));
    await screen.findByText(/Подтверждение отклонено/);
    expect(transport.post.mock.calls[1]?.[1]).toEqual(saved);
    expect(sessionStorage.getItem(key)).toBeNull();
    expect(transport.get.mock.calls.length).toBeGreaterThan(1);
  });

  it('blocks bounds, cancellation and storage failure before transport', async () => {
    mount(); await ready();
    fireEvent.change(screen.getByLabelText('Количество 4201'), { target: { value: '3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить возврат' }));
    await screen.findByText(/Укажите положительное количество/);
    fireEvent.change(screen.getByLabelText('Количество 4201'), { target: { value: '1' } });
    vi.mocked(window.confirm).mockReturnValueOnce(false);
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить возврат' }));
    expect(transport.post).not.toHaveBeenCalled();
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Unavailable'); });
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить возврат' }));
    await screen.findByText(/Не удалось сохранить подтверждение/);
    expect(transport.post).not.toHaveBeenCalled();
  });

  it('does not display zero remaining on load/error and clears protected quantities on forbidden', async () => {
    transport.get.mockImplementationOnce(() => new Promise(() => {}));
    const view = mount();
    expect(screen.getByText(/Загрузка подтверждённых/)).toBeInTheDocument();
    expect(screen.queryByText(/осталось/)).not.toBeInTheDocument();
    view.unmount();
    transport.get.mockRejectedValueOnce(httpError(503));
    const failed = mount();
    await screen.findByText(/Не удалось загрузить количества/);
    expect(screen.queryByLabelText('Количество 4201')).not.toBeInTheDocument();
    failed.unmount();
    transport.post.mockRejectedValueOnce(httpError(403));
    mount(); await ready();
    fireEvent.click(screen.getByRole('button', { name: 'Все оставшиеся' }));
    fireEvent.click(screen.getByRole('button', { name: 'Подтвердить возврат' }));
    await screen.findByText(/Нет доступа к подтверждениям/);
    expect(screen.queryByLabelText('Количество 4201')).not.toBeInTheDocument();
  });

  it('ordinary source 404 hides only the return control; bigint remains exact', async () => {
    transport.get.mockRejectedValueOnce(httpError(404));
    const view = mount();
    await waitFor(() => expect(screen.queryByText(/Загрузка/)).not.toBeInTheDocument());
    expect(screen.queryByText('Подтверждённый возврат товара кампании')).not.toBeInTheDocument();
    view.unmount();
    transport.get.mockResolvedValueOnce({ data: { ...summary, lines: [{ ...summary.lines[0], order_item_id: '9223372036854775807', paid_quantity: '9223372036854775807', returned_quantity: '9223372036854775806' }] } });
    mount();
    await screen.findByText(/осталось 1/);
    fireEvent.click(screen.getByRole('button', { name: 'Все оставшиеся' }));
    expect(screen.getByLabelText('Количество 9223372036854775807')).toHaveValue('1');
  });

  it('validates summary and receipt bytes at runtime, including mismatched order/intent', async () => {
    transport.get.mockResolvedValueOnce({ data: { ...summary, extra: 'private' } });
    await expect(referralReturnsApi.summary('100')).rejects.toThrow();
    transport.get.mockResolvedValueOnce({ data: { ...summary, order_id: '101' } });
    await expect(referralReturnsApi.summary('100')).rejects.toThrow();
    const intent = { return_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', lines: [{ order_item_id: '4201', quantity: '1' }] };
    const result = response(intent);
    result.data.receipt.lines = [{ order_item_id: '4202', quantity: '1' }];
    transport.post.mockResolvedValueOnce(result);
    await expect(referralReturnsApi.confirm('100', intent)).rejects.toThrow();
  });
});
