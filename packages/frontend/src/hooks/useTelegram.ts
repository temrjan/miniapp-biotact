import { useEffect, useState, useCallback } from 'react';
import WebApp from '@twa-dev/sdk';
import type { TelegramUser, Platform, ColorScheme } from '@/types';

// ═══════════════════════════════════════════════════════════════════
// Get Telegram User
// ═══════════════════════════════════════════════════════════════════

function getTelegramUser(): TelegramUser | null {
  const user = WebApp.initDataUnsafe.user;
  
  if (!user) {
    return null;
  }
  
  return {
    id: user.id,
    firstName: user.first_name,
    lastName: user.last_name,
    username: user.username,
    languageCode: user.language_code,
    isPremium: user.is_premium,
    photoUrl: user.photo_url,
  };
}

// ═══════════════════════════════════════════════════════════════════
// useTelegramUser Hook
// ═══════════════════════════════════════════════════════════════════

export function useTelegramUser(): TelegramUser | null {
  const [user] = useState(() => getTelegramUser());
  return user;
}

// ═══════════════════════════════════════════════════════════════════
// useColorScheme Hook
// ═══════════════════════════════════════════════════════════════════

export function useColorScheme(): ColorScheme {
  const [colorScheme, setColorScheme] = useState<ColorScheme>(WebApp.colorScheme);
  
  useEffect(() => {
    const handler = () => setColorScheme(WebApp.colorScheme);
    WebApp.onEvent('themeChanged', handler);
    
    return () => WebApp.offEvent('themeChanged', handler);
  }, []);
  
  return colorScheme;
}

// ═══════════════════════════════════════════════════════════════════
// usePlatform Hook
// ═══════════════════════════════════════════════════════════════════

export function usePlatform(): Platform {
  const platform = WebApp.platform;
  
  if (platform === 'ios') return 'ios';
  if (platform === 'android' || platform === 'android_x') return 'android';
  if (platform === 'weba' || platform === 'webk') return 'web';
  
  return 'unknown';
}

// ═══════════════════════════════════════════════════════════════════
// useViewport Hook
// ═══════════════════════════════════════════════════════════════════

interface Viewport {
  height: number;
  stableHeight: number;
  isExpanded: boolean;
}

export function useViewport(): Viewport {
  const [viewport, setViewport] = useState<Viewport>({
    height: WebApp.viewportHeight,
    stableHeight: WebApp.viewportStableHeight,
    isExpanded: WebApp.isExpanded,
  });
  
  useEffect(() => {
    const handler = () => {
      setViewport({
        height: WebApp.viewportHeight,
        stableHeight: WebApp.viewportStableHeight,
        isExpanded: WebApp.isExpanded,
      });
    };
    
    WebApp.onEvent('viewportChanged', handler);
    return () => WebApp.offEvent('viewportChanged', handler);
  }, []);
  
  return viewport;
}

// ═══════════════════════════════════════════════════════════════════
// useHaptic Hook
// ═══════════════════════════════════════════════════════════════════

interface HapticFeedback {
  light: () => void;
  medium: () => void;
  heavy: () => void;
  selection: () => void;
  success: () => void;
  error: () => void;
  warning: () => void;
}

export function useHaptic(): HapticFeedback {
  return {
    light: useCallback(() => WebApp.HapticFeedback.impactOccurred('light'), []),
    medium: useCallback(() => WebApp.HapticFeedback.impactOccurred('medium'), []),
    heavy: useCallback(() => WebApp.HapticFeedback.impactOccurred('heavy'), []),
    selection: useCallback(() => WebApp.HapticFeedback.selectionChanged(), []),
    success: useCallback(() => WebApp.HapticFeedback.notificationOccurred('success'), []),
    error: useCallback(() => WebApp.HapticFeedback.notificationOccurred('error'), []),
    warning: useCallback(() => WebApp.HapticFeedback.notificationOccurred('warning'), []),
  };
}

// ═══════════════════════════════════════════════════════════════════
// useInitData Hook
// ═══════════════════════════════════════════════════════════════════

export function useInitData(): string {
  return WebApp.initData;
}
