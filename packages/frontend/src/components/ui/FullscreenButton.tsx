import { Maximize2 } from 'lucide-react';
import { useState, useEffect } from 'react';
import { cn } from '@utils/cn';

export function FullscreenButton() {
  const tgWebApp = (window as unknown as { Telegram?: { WebApp?: Record<string, unknown> } }).Telegram?.WebApp;

  const [isFullscreen, setIsFullscreen] = useState(() => !!tgWebApp?.isFullscreen);
  const [isSupported] = useState(() => !!tgWebApp?.requestFullscreen);

  useEffect(() => {
    // Listen for fullscreen changes
    if (tgWebApp?.onEvent) {
      (tgWebApp.onEvent as (event: string, callback: () => void) => void)('fullscreenChanged', () => {
        setIsFullscreen(!!tgWebApp.isFullscreen);
      });
    }
  }, [tgWebApp]);

  const handleFullscreen = () => {
    if (!tgWebApp) return;

    try {
      if (isFullscreen) {
        (tgWebApp.exitFullscreen as (() => void) | undefined)?.();
      } else {
        (tgWebApp.requestFullscreen as (() => void) | undefined)?.();
      }
    } catch (e) {
      console.error('Fullscreen error:', e);
    }
  };

  // Don't show if not supported or already fullscreen
  if (!isSupported || isFullscreen) {
    return null;
  }

  return (
    <button
      onClick={handleFullscreen}
      className={cn(
        'fixed top-3 right-3 z-[100]',
        'w-10 h-10 flex items-center justify-center',
        'rounded-full bg-[var(--primary)] shadow-lg',
        'text-white hover:opacity-90 transition-opacity',
        'active:scale-95'
      )}
      aria-label="На весь экран"
    >
      <Maximize2 size={20} />
    </button>
  );
}
