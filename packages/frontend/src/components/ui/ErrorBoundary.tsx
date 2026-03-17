import type { ReactNode, ErrorInfo } from 'react';
import { ErrorBoundary as ReactErrorBoundary } from 'react-error-boundary';
import { ErrorState } from './ErrorState';
import { getErrorType } from './ErrorState.utils';

// ═══════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode | ((error: Error, reset: () => void) => ReactNode);
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
}

interface ErrorFallbackProps {
  error: Error;
  resetErrorBoundary: () => void;
}

// ═══════════════════════════════════════════════════════════════════
// Fallback Component
// ═══════════════════════════════════════════════════════════════════

function DefaultErrorFallback({ error, resetErrorBoundary }: ErrorFallbackProps) {
  const errorType = getErrorType(error);

  return (
    <ErrorState
      type={errorType}
      title="Что-то пошло не так"
      message={error.message}
      onRetry={resetErrorBoundary}
    />
  );
}

// ═══════════════════════════════════════════════════════════════════
// Component
// ═══════════════════════════════════════════════════════════════════

export function ErrorBoundary({ children, fallback, onError }: ErrorBoundaryProps) {
  const handleError = (error: Error, errorInfo: ErrorInfo) => {
    onError?.(error, errorInfo);
    
    // Log error for debugging
    console.error('ErrorBoundary caught:', error, errorInfo);
  };

  // Custom fallback provided
  if (fallback) {
    const FallbackComponent = typeof fallback === 'function'
      ? ({ error, resetErrorBoundary }: ErrorFallbackProps) => fallback(error, resetErrorBoundary)
      : () => <>{fallback}</>;

    return (
      <ReactErrorBoundary
        FallbackComponent={FallbackComponent}
        onError={handleError}
      >
        {children}
      </ReactErrorBoundary>
    );
  }

  // Default fallback
  return (
    <ReactErrorBoundary
      FallbackComponent={DefaultErrorFallback}
      onError={handleError}
    >
      {children}
    </ReactErrorBoundary>
  );
}
