import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import { RedeemReserveNotice } from './RedeemReserveNotice';

const fmt = (n: number) => String(n);

describe('RedeemReserveNotice', () => {
  it('показывает, сколько баллов занято и после какого времени пробовать снова', () => {
    render(
      <RedeemReserveNotice
        reservedPoints={17_300}
        reservedUntil={new Date('2026-07-23T09:35:00+05:00')}
        formatPrice={fmt}
      />
    );

    const notice = screen.getByTestId('redeem-reserve-notice');
    expect(notice.textContent).toContain('17300');
    expect(notice.textContent).toContain('09:35');
    // 24-часовой формат проверяется ОТДЕЛЬНО: `toContain('09:35')` прошло бы и на «09:35 AM»,
    // то есть не отличило бы явную ru-RU локаль от формата устройства — ради чего тест и написан.
    expect(notice.textContent).not.toMatch(/AM|PM/);
  });

  it('без известного срока показывает только сумму, без обрывка про время', () => {
    render(<RedeemReserveNotice reservedPoints={5_000} reservedUntil={null} formatPrice={fmt} />);

    const notice = screen.getByTestId('redeem-reserve-notice');
    expect(notice.textContent).toContain('5000');
    expect(notice.textContent).not.toContain('после');
  });

  it('без резерва не рендерит ничего', () => {
    // Главный случай прод-дефекта — резерв съел всё доступное и степпера нет. Обратный случай
    // тоже обязан быть чистым: без резерва на чекауте не должно появляться лишней плашки.
    const { container } = render(
      <RedeemReserveNotice reservedPoints={0} reservedUntil={null} formatPrice={fmt} />
    );

    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId('redeem-reserve-notice')).not.toBeInTheDocument();
  });
});
