// Telegram WebApp extended types for Mini Apps 2.0
declare global {
  interface Window {
    Telegram?: {
      WebApp?: {
        // Core methods
        ready: () => void;
        expand: () => void;
        close: () => void;
        
        // Fullscreen (Mini Apps 2.0)
        requestFullscreen?: () => void;
        exitFullscreen?: () => void;
        isFullscreen?: boolean;
        
        // Header
        setHeaderColor: (color: string) => void;
        setBackgroundColor: (color: string) => void;
        
        // Closing
        enableClosingConfirmation: () => void;
        disableClosingConfirmation: () => void;
      };
    };
  }
}

export {};
