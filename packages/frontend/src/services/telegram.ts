import WebApp from '@twa-dev/sdk';
import type { TelegramUser, Platform, ColorScheme } from '@/types';

// ═══════════════════════════════════════════════════════════════════
// User Information
// ═══════════════════════════════════════════════════════════════════

export function getTelegramUser(): TelegramUser | null {
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
// Init Data (for backend validation)
// ═══════════════════════════════════════════════════════════════════

export function getInitData(): string {
  return WebApp.initData;
}

export function getInitDataUnsafe() {
  return WebApp.initDataUnsafe;
}

// ═══════════════════════════════════════════════════════════════════
// Platform & Theme
// ═══════════════════════════════════════════════════════════════════

export function getPlatform(): Platform {
  const platform = WebApp.platform;
  
  if (platform === 'ios') return 'ios';
  if (platform === 'android' || platform === 'android_x') return 'android';
  if (platform === 'weba' || platform === 'webk') return 'web';
  
  return 'unknown';
}

export function getColorScheme(): ColorScheme {
  return WebApp.colorScheme;
}

export function getThemeParams() {
  return WebApp.themeParams;
}

// ═══════════════════════════════════════════════════════════════════
// Viewport
// ═══════════════════════════════════════════════════════════════════

export function getViewport() {
  return {
    height: WebApp.viewportHeight,
    stableHeight: WebApp.viewportStableHeight,
    isExpanded: WebApp.isExpanded,
  };
}

export function expandApp() {
  WebApp.expand();
}

// ═══════════════════════════════════════════════════════════════════
// Haptic Feedback
// ═══════════════════════════════════════════════════════════════════

export const haptic = {
  light: () => WebApp.HapticFeedback.impactOccurred('light'),
  medium: () => WebApp.HapticFeedback.impactOccurred('medium'),
  heavy: () => WebApp.HapticFeedback.impactOccurred('heavy'),
  selection: () => WebApp.HapticFeedback.selectionChanged(),
  success: () => WebApp.HapticFeedback.notificationOccurred('success'),
  error: () => WebApp.HapticFeedback.notificationOccurred('error'),
  warning: () => WebApp.HapticFeedback.notificationOccurred('warning'),
};

// ═══════════════════════════════════════════════════════════════════
// Alerts & Popups
// ═══════════════════════════════════════════════════════════════════

export function showAlert(message: string): Promise<void> {
  return new Promise((resolve) => {
    WebApp.showAlert(message, resolve);
  });
}

export function showConfirm(message: string): Promise<boolean> {
  return new Promise((resolve) => {
    WebApp.showConfirm(message, resolve);
  });
}

export function showPopup(
  title: string,
  message: string
): Promise<string> {
  return new Promise((resolve) => {
    WebApp.showPopup(
      { title, message, buttons: [{ type: 'ok' }] },
      (buttonId) => resolve(buttonId ?? '')
    );
  });
}

// ═══════════════════════════════════════════════════════════════════
// App Control
// ═══════════════════════════════════════════════════════════════════

export function closeApp() {
  WebApp.close();
}

export function enableClosingConfirmation() {
  WebApp.enableClosingConfirmation();
}

export function disableClosingConfirmation() {
  WebApp.disableClosingConfirmation();
}

// ═══════════════════════════════════════════════════════════════════
// Links
// ═══════════════════════════════════════════════════════════════════

export function openLink(url: string, tryInstantView = false) {
  WebApp.openLink(url, { try_instant_view: tryInstantView });
}

export function openTelegramLink(url: string) {
  WebApp.openTelegramLink(url);
}

// ═══════════════════════════════════════════════════════════════════
// Cloud Storage
// ═══════════════════════════════════════════════════════════════════

export const cloudStorage = {
  async get(key: string): Promise<string | null> {
    return new Promise((resolve, reject) => {
      WebApp.CloudStorage.getItem(key, (error, value) => {
        if (error) {
          reject(new Error(error));
        } else {
          resolve(value ?? null);
        }
      });
    });
  },
  
  async set(key: string, value: string): Promise<void> {
    return new Promise((resolve, reject) => {
      WebApp.CloudStorage.setItem(key, value, (error) => {
        if (error) {
          reject(new Error(error));
        } else {
          resolve();
        }
      });
    });
  },
  
  async remove(key: string): Promise<void> {
    return new Promise((resolve, reject) => {
      WebApp.CloudStorage.removeItem(key, (error) => {
        if (error) {
          reject(new Error(error));
        } else {
          resolve();
        }
      });
    });
  },
  
  async getKeys(): Promise<string[]> {
    return new Promise((resolve, reject) => {
      WebApp.CloudStorage.getKeys((error, keys) => {
        if (error) {
          reject(new Error(error));
        } else {
          resolve(keys ?? []);
        }
      });
    });
  },
};
