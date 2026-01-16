import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ErrorState, getErrorType } from './ErrorState';

describe('ErrorState', () => {
  it('renders with default generic error', () => {
    render(<ErrorState />);
    expect(screen.getByText('Произошла ошибка')).toBeInTheDocument();
    expect(screen.getByText('Что-то пошло не так. Попробуйте ещё раз.')).toBeInTheDocument();
  });

  it('renders network error type', () => {
    render(<ErrorState type="network" />);
    expect(screen.getByText('Нет соединения')).toBeInTheDocument();
    expect(screen.getByText('Проверьте подключение к интернету и попробуйте снова.')).toBeInTheDocument();
  });

  it('renders notFound error type', () => {
    render(<ErrorState type="notFound" />);
    expect(screen.getByText('Не найдено')).toBeInTheDocument();
    expect(screen.getByText('Запрашиваемый ресурс не существует или был удалён.')).toBeInTheDocument();
  });

  it('renders server error type', () => {
    render(<ErrorState type="server" />);
    expect(screen.getByText('Ошибка сервера')).toBeInTheDocument();
    expect(screen.getByText('Сервер временно недоступен. Попробуйте позже.')).toBeInTheDocument();
  });

  it('renders custom title and message', () => {
    render(
      <ErrorState
        title="Custom Title"
        message="Custom message text"
      />
    );
    expect(screen.getByText('Custom Title')).toBeInTheDocument();
    expect(screen.getByText('Custom message text')).toBeInTheDocument();
  });

  it('renders retry button when onRetry is provided', () => {
    const handleRetry = vi.fn();
    render(<ErrorState onRetry={handleRetry} />);

    const retryButton = screen.getByRole('button', { name: /попробовать снова/i });
    expect(retryButton).toBeInTheDocument();
  });

  it('does not render retry button when onRetry is not provided', () => {
    render(<ErrorState />);
    expect(screen.queryByRole('button', { name: /попробовать снова/i })).not.toBeInTheDocument();
  });

  it('calls onRetry when retry button is clicked', () => {
    const handleRetry = vi.fn();
    render(<ErrorState onRetry={handleRetry} />);

    fireEvent.click(screen.getByRole('button', { name: /попробовать снова/i }));
    expect(handleRetry).toHaveBeenCalledTimes(1);
  });

  it('applies custom className', () => {
    const { container } = render(<ErrorState className="custom-class" />);
    const errorState = container.firstChild as HTMLElement;
    expect(errorState).toHaveClass('custom-class');
  });
});

describe('getErrorType', () => {
  it('returns network for network errors', () => {
    const error = new Error('Network error occurred');
    expect(getErrorType(error)).toBe('network');
  });

  it('returns network for fetch errors', () => {
    const error = new Error('Failed to fetch');
    expect(getErrorType(error)).toBe('network');
  });

  it('returns notFound for 404 errors', () => {
    const error = new Error('404 - Not found');
    expect(getErrorType(error)).toBe('notFound');
  });

  it('returns notFound for not found errors', () => {
    const error = new Error('Resource not found');
    expect(getErrorType(error)).toBe('notFound');
  });

  it('returns server for 500 errors', () => {
    const error = new Error('500 - Internal Server Error');
    expect(getErrorType(error)).toBe('server');
  });

  it('returns server for server errors', () => {
    const error = new Error('Server is down');
    expect(getErrorType(error)).toBe('server');
  });

  it('returns generic for unknown errors', () => {
    const error = new Error('Something unexpected happened');
    expect(getErrorType(error)).toBe('generic');
  });

  it('returns generic for non-Error objects', () => {
    expect(getErrorType('string error')).toBe('generic');
    expect(getErrorType(null)).toBe('generic');
    expect(getErrorType(undefined)).toBe('generic');
    expect(getErrorType({ message: 'object error' })).toBe('generic');
  });
});
