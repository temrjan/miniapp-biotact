/**
 * useGeolocation Hook
 *
 * Provides browser geolocation with high accuracy.
 * Telegram LocationManager not used because Browser Geolocation API
 * showed 13m accuracy in tests, which is sufficient for delivery services.
 *
 * @example
 * ```tsx
 * const { requestLocation, location, isLoading, error } = useGeolocation({
 *   onSuccess: (coords) => console.log(coords),
 *   onError: (err) => console.error(err),
 * });
 * ```
 */

import { useState, useCallback } from 'react';

// ═══════════════════════════════════════════════════════════════════
// Types
// ═══════════════════════════════════════════════════════════════════

interface GeolocationCoords {
  latitude: number;
  longitude: number;
  accuracy: number; // meters
}

interface UseGeolocationOptions {
  /**
   * Callback when location is successfully obtained
   */
  onSuccess?: (coords: GeolocationCoords) => void | Promise<void>;

  /**
   * Callback when error occurs
   */
  onError?: (error: string) => void;

  /**
   * Request location automatically on mount
   * @default false
   */
  autoRequest?: boolean;

  /**
   * Geolocation timeout in milliseconds
   * @default 10000 (10 seconds)
   */
  timeout?: number;
}

interface UseGeolocationReturn {
  /**
   * Current location data
   */
  location: GeolocationCoords | null;

  /**
   * Loading state during geolocation request
   */
  isLoading: boolean;

  /**
   * Error message if geolocation failed
   */
  error: string | null;

  /**
   * Whether Geolocation API is supported
   */
  isSupported: boolean;

  /**
   * Trigger geolocation request
   */
  requestLocation: () => void;

  /**
   * Clear error state
   */
  clearError: () => void;
}

// ═══════════════════════════════════════════════════════════════════
// Error Messages
// ═══════════════════════════════════════════════════════════════════

const ERROR_MESSAGES = {
  NOT_SUPPORTED: 'Геолокация не поддерживается вашим браузером',
  PERMISSION_DENIED: 'Доступ к геолокации запрещён. Разрешите доступ в настройках браузера.',
  POSITION_UNAVAILABLE: 'Не удалось определить местоположение. Проверьте подключение к интернету.',
  TIMEOUT: 'Превышено время ожидания. Попробуйте ещё раз.',
  UNKNOWN: 'Произошла неизвестная ошибка при определении местоположения',
} as const;

// ═══════════════════════════════════════════════════════════════════
// Helper: Get Error Message
// ═══════════════════════════════════════════════════════════════════

function getErrorMessage(error: GeolocationPositionError): string {
  switch (error.code) {
    case error.PERMISSION_DENIED:
      return ERROR_MESSAGES.PERMISSION_DENIED;
    case error.POSITION_UNAVAILABLE:
      return ERROR_MESSAGES.POSITION_UNAVAILABLE;
    case error.TIMEOUT:
      return ERROR_MESSAGES.TIMEOUT;
    default:
      return ERROR_MESSAGES.UNKNOWN;
  }
}

// ═══════════════════════════════════════════════════════════════════
// Hook: useGeolocation
// ═══════════════════════════════════════════════════════════════════

export function useGeolocation(options: UseGeolocationOptions = {}): UseGeolocationReturn {
  const {
    onSuccess,
    onError,
    // autoRequest = false, // Not implemented
    timeout = 10000,
  } = options;

  // ═══════════════════════════════════════════════════════════════════
  // State
  // ═══════════════════════════════════════════════════════════════════

  const [location, setLocation] = useState<GeolocationCoords | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isSupported = 'geolocation' in navigator;

  // ═══════════════════════════════════════════════════════════════════
  // Handlers
  // ═══════════════════════════════════════════════════════════════════

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  const requestLocation = useCallback(() => {
    // Check support
    if (!isSupported) {
      const errorMsg = ERROR_MESSAGES.NOT_SUPPORTED;
      setError(errorMsg);
      onError?.(errorMsg);
      return;
    }

    setIsLoading(true);
    setError(null);

    navigator.geolocation.getCurrentPosition(
      // Success callback
      async (position) => {
        const coords: GeolocationCoords = {
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracy: position.coords.accuracy,
        };

        setLocation(coords);
        setIsLoading(false);

        // Call success callback
        try {
          await onSuccess?.(coords);
        } catch (err) {
          console.error('[useGeolocation] Error in onSuccess callback:', err);
        }
      },

      // Error callback
      (err) => {
        const errorMsg = getErrorMessage(err);
        setError(errorMsg);
        setIsLoading(false);
        onError?.(errorMsg);
      },

      // Options for high accuracy
      {
        enableHighAccuracy: true, // Use GPS if available
        timeout, // Maximum wait time
        maximumAge: 0, // Don't use cached position
      }
    );
  }, [isSupported, timeout, onSuccess, onError]);

  // ═══════════════════════════════════════════════════════════════════
  // Auto-request on mount
  // ═══════════════════════════════════════════════════════════════════

  // Note: useEffect is NOT used here to avoid auto-request side effects
  // If autoRequest is needed, implement in component with useEffect

  // ═══════════════════════════════════════════════════════════════════
  // Return
  // ═══════════════════════════════════════════════════════════════════

  return {
    location,
    isLoading,
    error,
    isSupported,
    requestLocation,
    clearError,
  };
}

// ═══════════════════════════════════════════════════════════════════
// Export Error Messages for i18n
// ═══════════════════════════════════════════════════════════════════

export { ERROR_MESSAGES };
export type { GeolocationCoords, UseGeolocationOptions, UseGeolocationReturn };
