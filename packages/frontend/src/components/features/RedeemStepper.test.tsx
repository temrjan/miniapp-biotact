import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { RedeemStepper } from './RedeemStepper';

// balance 100 000, goods 69 000 → maxRedeemable = 69 000 (full cover), partial ceiling 64 000.
const money = { balance: 100_000, subtotal: 69_000, total: 69_000 };
const fmt = (n: number) => String(n);

function renderStepper(value = 0) {
  const onChange = vi.fn();
  render(<RedeemStepper {...money} value={value} onChange={onChange} formatPrice={fmt} />);
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
});
