import type { HTMLAttributes } from 'react';
import { AlertCircle, RefreshCw, WifiOff } from 'lucide-react';
import { Button } from './Button';
import { cn } from '@utils/cn';

// ═══════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════

type ErrorType = 'generic' | 'network' | 'notFound' | 'server';

interface ErrorStateProps extends HTMLAttributes<HTMLDivElement> {
  type?: ErrorType;
  title?: string;
  message?: string;
  onRetry?: () => void;
}

// ═══════════════════════════════════════════════════════════════════
// Constants
// ═══════════════════════════════════════════════════════════════════

const ERROR_CONFIGS: Record<ErrorType, { icon: typeof AlertCircle; title: string; message: string }> = {
  generic: {
    icon: AlertCircle,
    title: 'Произошла ошибка',
    message: 'Что-то пошло не так. Попробуйте ещё раз.',
  },
  network: {
    icon: WifiOff,
    title: 'Нет соединения',
    message: 'Проверьте подключение к интернету и попробуйте снова.',
  },
  notFound: {
    icon: AlertCircle,
    title: 'Не найдено',
    message: 'Запрашиваемый ресурс не существует или был удалён.',
  },
  server: {
    icon: AlertCircle,
    title: 'Ошибка сервера',
    message: 'Сервер временно недоступен. Попробуйте позже.',
  },
};

// ═══════════════════════════════════════════════════════════════════
// Component
// ═══════════════════════════════════════════════════════════════════

export function ErrorState({
  type = 'generic',
  title,
  message,
  onRetry,
  className,
  ...props
}: ErrorStateProps) {
  const config = ERROR_CONFIGS[type];
  const Icon = config.icon;

  const displayTitle = title ?? config.title;
  const displayMessage = message ?? config.message;

  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center min-h-[300px] p-6 text-center',
        className
      )}
      {...props}
    >
      <div className="neumorphic w-20 h-20 rounded-full flex items-center justify-center mb-4">
        <Icon size={32} className="text-[var(--error)]" />
      </div>

      <h2 className="text-xl font-bold text-[var(--text-primary)] mb-2">
        {displayTitle}
      </h2>

      <p className="text-[var(--text-secondary)] mb-6 max-w-[280px]">
        {displayMessage}
      </p>

      {onRetry && (
        <Button variant="primary" onClick={onRetry}>
          <RefreshCw size={18} className="mr-2" />
          Попробовать снова
        </Button>
      )}
    </div>
  );
}

