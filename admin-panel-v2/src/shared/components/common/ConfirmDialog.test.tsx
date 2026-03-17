import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { ConfirmDialog } from './ConfirmDialog';

describe('ConfirmDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    title: 'Удалить товар?',
    description: 'Это действие нельзя отменить',
    onConfirm: vi.fn(),
  };

  it('renders title and description', () => {
    render(<ConfirmDialog {...defaultProps} />);
    expect(screen.getByText('Удалить товар?')).toBeInTheDocument();
    expect(screen.getByText('Это действие нельзя отменить')).toBeInTheDocument();
  });

  it('renders default button texts', () => {
    render(<ConfirmDialog {...defaultProps} />);
    expect(screen.getByRole('button', { name: 'Подтвердить' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Отмена' })).toBeInTheDocument();
  });

  it('renders custom button texts', () => {
    render(
      <ConfirmDialog
        {...defaultProps}
        confirmText="Да, удалить"
        cancelText="Нет"
      />
    );
    expect(screen.getByRole('button', { name: 'Да, удалить' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Нет' })).toBeInTheDocument();
  });

  it('calls onConfirm when confirm button clicked', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<ConfirmDialog {...defaultProps} onConfirm={onConfirm} />);

    await user.click(screen.getByRole('button', { name: 'Подтвердить' }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it('calls onOpenChange(false) when cancel clicked', async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(<ConfirmDialog {...defaultProps} onOpenChange={onOpenChange} />);

    await user.click(screen.getByRole('button', { name: 'Отмена' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('disables buttons when loading', () => {
    render(<ConfirmDialog {...defaultProps} isLoading />);
    expect(screen.getByRole('button', { name: 'Загрузка...' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Отмена' })).toBeDisabled();
  });

  it('does not render when closed', () => {
    render(<ConfirmDialog {...defaultProps} open={false} />);
    expect(screen.queryByText('Удалить товар?')).not.toBeInTheDocument();
  });
});
