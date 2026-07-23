import type { ReactNode } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { api } from '@/api/client';
import { RedeemReserveNotice } from './RedeemReserveNotice';

vi.mock('@/api/client', () => ({
  api: { get: vi.fn(), post: vi.fn() },
}));

const postMock = vi.mocked(api.post);
const fmt = (n: number) => String(n);

let queryClient: QueryClient;

function wrap(ui: ReactNode) {
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

function renderNotice(reservedPoints = 17_300, reservedUntil: Date | null = null) {
  return wrap(
    <RedeemReserveNotice
      reservedPoints={reservedPoints}
      reservedUntil={reservedUntil}
      formatPrice={fmt}
    />
  );
}

beforeEach(() => {
  postMock.mockReset();
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
});

describe('RedeemReserveNotice', () => {
  it('показывает, сколько баллов занято и после какого времени пробовать снова', () => {
    // Дата строится из ЛОКАЛЬНЫХ компонентов, а не из ISO со смещением: компонент печатает время
    // в поясе устройства (это и правильно — клиенту нужны его часы), поэтому фиксированный момент
    // «09:35+05:00» рендерился бы как 09:35 в Ташкенте и 04:35 в UTC. Первая версия теста была
    // именно такой и упала в CI, зелёная локально. Так тест проверяет формат, а не пояс раннера.
    renderNotice(17_300, new Date(2026, 6, 23, 9, 35));

    const notice = screen.getByTestId('redeem-reserve-notice');
    expect(notice.textContent).toContain('17300');
    expect(notice.textContent).toContain('09:35');
    // 24-часовой формат проверяется ОТДЕЛЬНО: `toContain('09:35')` прошло бы и на «09:35 AM»,
    // то есть не отличило бы явную ru-RU локаль от формата устройства — ради чего тест и написан.
    expect(notice.textContent).not.toMatch(/AM|PM/);
  });

  it('без известного срока показывает только сумму, без обрывка про время', () => {
    renderNotice(5_000, null);

    const notice = screen.getByTestId('redeem-reserve-notice');
    expect(notice.textContent).toContain('5000');
    expect(notice.textContent).not.toContain('после');
  });

  it('без резерва не рендерит ничего', () => {
    // Главный случай прод-дефекта — резерв съел всё доступное и степпера нет. Обратный случай
    // тоже обязан быть чистым: без резерва на чекауте не должно появляться лишней плашки.
    const { container } = renderNotice(0, null);

    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId('redeem-reserve-notice')).not.toBeInTheDocument();
  });

  it('клик по «Вернуть баллы» зовёт эндпоинт и освежает снимок лояльности', async () => {
    postMock.mockResolvedValue({ released_orders: 1, released_points: 17_300, blocked_orders: 0 });
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    renderNotice();

    fireEvent.click(screen.getByRole('button'));

    await waitFor(() => {
      expect(postMock).toHaveBeenCalledWith('/orders/reservations/release', {});
    });
    // Без инвалидации плашка осталась бы висеть с уже освобождёнными баллами.
    await waitFor(() => {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ['loyalty', 'me'] });
    });
  });

  it('во время запроса кнопка заблокирована — двойной клик не шлёт второй запрос', async () => {
    let resolve: (value: unknown) => void = () => {};
    postMock.mockImplementation(() => new Promise((r) => (resolve = r)));
    renderNotice();

    const button = screen.getByRole('button');
    fireEvent.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    fireEvent.click(button);

    expect(postMock).toHaveBeenCalledTimes(1);
    resolve({ released_orders: 1, released_points: 17_300, blocked_orders: 0 });
  });

  it('отказ сервера показывает нейтральный текст, не выдумывая причину', async () => {
    // 409 приходит и когда счёт уже оплачен, и когда лёг транспорт — сервер сам их не различает
    // (delete_invoice возвращает False в обоих случаях), поэтому UI не вправе называть причину.
    postMock.mockRejectedValue(new Error('conflict'));
    renderNotice();

    fireEvent.click(screen.getByRole('button'));

    const error = await screen.findByTestId('release-error');
    expect(error.textContent).toBeTruthy();
    expect(error.textContent).not.toMatch(/оплачен|paid/i);
  });

  it('снимок лояльности освежается и после ошибки — состояние на сервере могло измениться', async () => {
    postMock.mockRejectedValue(new Error('conflict'));
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');
    renderNotice();

    fireEvent.click(screen.getByRole('button'));

    await waitFor(() => {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: ['loyalty', 'me'] });
    });
  });
});
