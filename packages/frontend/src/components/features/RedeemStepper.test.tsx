import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { RedeemStepper } from './RedeemStepper';

// balance 100 000, goods 69 000 → maxRedeemable = 69 000 (full cover), partial ceiling 64 000.
const money = { balance: 100_000, available: 100_000, subtotal: 69_000, total: 69_000 };
const fmt = (n: number) => String(n);

function renderStepper(value = 0, overrides: Partial<typeof money> = {}) {
  const onChange = vi.fn();
  render(
    <RedeemStepper
      {...money}
      {...overrides}
      value={value}
      onChange={onChange}
      formatPrice={fmt}
    />
  );
  return { onChange };
}

describe('RedeemStepper', () => {
  it('слайдер: max = maxRedeemable, шаг 100', () => {
    renderStepper();
    const slider = screen.getByRole('slider') as HTMLInputElement;
    expect(slider.max).toBe('69000');
    expect(slider.step).toBe('100');
  });

  it('«Максимум» → onChange(maxRedeemable)', () => {
    const { onChange } = renderStepper();
    fireEvent.click(screen.getByRole('button'));
    expect(onChange).toHaveBeenCalledWith(69_000);
  });

  it('перетаскивание в мёртвую зону → onChange снапит вниз к partial-потолку', () => {
    const { onChange } = renderStepper();
    // 66 000 оставило бы картой 3 000 (мёртвая зона 1..4999) → снап к 64 000.
    fireEvent.change(screen.getByRole('slider'), { target: { value: '66000' } });
    expect(onChange).toHaveBeenCalledWith(64_000);
  });

  it('живой пересчёт «к оплате картой» = total − value', () => {
    renderStepper(4_000);
    expect(screen.getByText('65000')).toBeInTheDocument(); // 69 000 − 4 000
  });

  // Резерв Модели-1: часть баллов удерживает собственный незавершённый заказ. Ползунок обязан
  // считаться от доступного, иначе предложит сумму, которую сервер гарантированно отобьёт 409-м.
  it('потолок считается от available, а не от balance', () => {
    renderStepper(0, { available: 10_000 });
    const slider = screen.getByRole('slider') as HTMLInputElement;
    // 10 000 доступно (при балансе 100 000): полное покрытие недостижимо, потолок = сам available.
    expect(slider.max).toBe('10000');
  });

  it('при резерве подпись — «Доступно», и показывает available, а не баланс', () => {
    renderStepper(0, { available: 10_000 });
    expect(screen.getByText(/Доступно/)).toBeInTheDocument();
    expect(screen.getByText(/Доступно: 10000/)).toBeInTheDocument();
    expect(screen.queryByText(/Баланс/)).not.toBeInTheDocument();
  });

  it('без резерва подпись остаётся «Баланс»', () => {
    renderStepper();
    expect(screen.getByText(/Баланс: 100000/)).toBeInTheDocument();
    expect(screen.queryByText(/Доступно/)).not.toBeInTheDocument();
  });
});
