import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ShoppingCart } from 'lucide-react';
import { StatsCard } from './StatsCard';

describe('StatsCard', () => {
  it('renders title and value', () => {
    render(<StatsCard title="Заказы" value={42} />);
    expect(screen.getByText('Заказы')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
  });

  it('renders string value', () => {
    render(<StatsCard title="Выручка" value="1 250 000 сум" />);
    expect(screen.getByText('1 250 000 сум')).toBeInTheDocument();
  });

  it('renders subtitle when provided', () => {
    render(<StatsCard title="Заказы" value={42} subtitle="+5 за сегодня" />);
    expect(screen.getByText('+5 за сегодня')).toBeInTheDocument();
  });

  it('does not render subtitle when not provided', () => {
    render(<StatsCard title="Заказы" value={42} />);
    expect(screen.queryByText('+5 за сегодня')).not.toBeInTheDocument();
  });

  it('renders skeleton when loading', () => {
    render(<StatsCard title="Заказы" value={42} isLoading />);
    expect(screen.queryByText('42')).not.toBeInTheDocument();
    expect(screen.queryByText('Заказы')).not.toBeInTheDocument();
  });

  it('renders icon when provided', () => {
    render(<StatsCard title="Заказы" value={42} icon={ShoppingCart} />);
    // Icon renders as svg
    const svgs = document.querySelectorAll('svg');
    expect(svgs.length).toBeGreaterThan(0);
  });
});
