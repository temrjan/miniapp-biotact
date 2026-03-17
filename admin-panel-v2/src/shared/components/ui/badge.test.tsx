import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { Badge } from './badge';

describe('Badge', () => {
  it('renders with text', () => {
    render(<Badge>Активен</Badge>);
    expect(screen.getByText('Активен')).toBeInTheDocument();
  });

  it('applies default variant classes', () => {
    render(<Badge>Default</Badge>);
    const badge = screen.getByText('Default');
    expect(badge.className).toContain('bg-primary');
  });

  it('applies destructive variant', () => {
    render(<Badge variant="destructive">Удалён</Badge>);
    const badge = screen.getByText('Удалён');
    expect(badge.className).toContain('destructive');
  });

  it('applies secondary variant', () => {
    render(<Badge variant="secondary">Черновик</Badge>);
    const badge = screen.getByText('Черновик');
    expect(badge.className).toContain('secondary');
  });

  it('applies outline variant', () => {
    render(<Badge variant="outline">Ожидание</Badge>);
    const badge = screen.getByText('Ожидание');
    expect(badge.className).toContain('text-foreground');
  });

  it('accepts custom className', () => {
    render(<Badge className="my-custom-class">Custom</Badge>);
    const badge = screen.getByText('Custom');
    expect(badge.className).toContain('my-custom-class');
  });
});
