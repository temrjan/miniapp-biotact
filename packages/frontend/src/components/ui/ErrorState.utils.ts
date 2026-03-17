type ErrorType = 'generic' | 'network' | 'notFound' | 'server';

export function getErrorType(error: unknown): ErrorType {
  if (error instanceof Error) {
    const message = error.message.toLowerCase();

    if (message.includes('network') || message.includes('fetch')) {
      return 'network';
    }

    if (message.includes('404') || message.includes('not found')) {
      return 'notFound';
    }

    if (message.includes('500') || message.includes('server')) {
      return 'server';
    }
  }

  return 'generic';
}
