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

  // Гейт-1, уточнение 2: поведение БЕЗ confirmValue закреплено тестом — поля нет, кнопка активна.
  it('without confirmValue renders no input and an enabled confirm button', () => {
    render(<ConfirmDialog {...defaultProps} />);
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Подтвердить' })).toBeEnabled();
  });

  it('with confirmValue keeps confirm disabled until the exact value is typed', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(<ConfirmDialog {...defaultProps} confirmValue="7" onConfirm={onConfirm} />);

    const confirm = screen.getByRole('button', { name: 'Подтвердить' });
    expect(confirm).toBeDisabled();

    await user.type(screen.getByRole('textbox'), '3');
    expect(confirm).toBeDisabled();

    await user.clear(screen.getByRole('textbox'));
    await user.type(screen.getByRole('textbox'), '7');
    expect(confirm).toBeEnabled();

    await user.click(confirm);
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it('with confirmValue does not reveal the expected value in the field itself', () => {
    // The typed confirmation exists to make the administrator READ the number in the description
    // and reproduce it consciously. A placeholder that spells the answer inside the very field they
    // are typing into turns that into "retype what you see" — the reflex the guard is built against
    // (Gate-2 МИНОР-1 on the winners dialog, 2026-08-17). An empty placeholder is acceptable; one
    // equal to the answer is not.
    render(<ConfirmDialog {...defaultProps} confirmValue="7" />);
    expect(screen.getByRole('textbox')).not.toHaveAttribute('placeholder', '7');
  });

  it('with confirmValue accepts the value with surrounding whitespace', async () => {
    const user = userEvent.setup();
    render(<ConfirmDialog {...defaultProps} confirmValue="7" />);

    await user.type(screen.getByRole('textbox'), '  7 ');
    expect(screen.getByRole('button', { name: 'Подтвердить' })).toBeEnabled();
  });

  it('with confirmValue resets the input when reopened', async () => {
    const { rerender } = render(<ConfirmDialog {...defaultProps} confirmValue="7" open={false} />);
    rerender(<ConfirmDialog {...defaultProps} confirmValue="7" open />);

    const user = userEvent.setup();
    await user.type(screen.getByRole('textbox'), '7');
    rerender(<ConfirmDialog {...defaultProps} confirmValue="7" open={false} />);
    rerender(<ConfirmDialog {...defaultProps} confirmValue="7" open />);

    expect(screen.getByRole('textbox')).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Подтвердить' })).toBeDisabled();
  });
});
