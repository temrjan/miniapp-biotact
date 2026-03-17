import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi } from 'vitest';
import { LoginPage } from './LoginPage';

// Mock useLogin hook
const mockMutate = vi.fn();
vi.mock('../hooks/useAuth', () => ({
  useLogin: () => ({
    mutate: mockMutate,
    isPending: false,
    isError: false,
  }),
}));

describe('LoginPage', () => {
  it('renders login form', () => {
    render(<LoginPage />);
    expect(screen.getByText('Biotact Admin')).toBeInTheDocument();
    expect(screen.getByText('Вход в панель управления')).toBeInTheDocument();
    expect(screen.getByLabelText('Имя пользователя')).toBeInTheDocument();
    expect(screen.getByLabelText('Пароль')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Войти' })).toBeInTheDocument();
  });

  it('shows validation errors on empty submit', async () => {
    const user = userEvent.setup();
    render(<LoginPage />);

    await user.click(screen.getByRole('button', { name: 'Войти' }));

    expect(await screen.findByText('Введите имя пользователя')).toBeInTheDocument();
    expect(await screen.findByText('Введите пароль')).toBeInTheDocument();
  });

  it('calls login on valid submit', async () => {
    const user = userEvent.setup();
    render(<LoginPage />);

    await user.type(screen.getByLabelText('Имя пользователя'), 'admin');
    await user.type(screen.getByLabelText('Пароль'), 'password123');
    await user.click(screen.getByRole('button', { name: 'Войти' }));

    expect(mockMutate).toHaveBeenCalledWith({
      username: 'admin',
      password: 'password123',
    });
  });

  it('has password field with type password', () => {
    render(<LoginPage />);
    expect(screen.getByLabelText('Пароль')).toHaveAttribute('type', 'password');
  });
});
