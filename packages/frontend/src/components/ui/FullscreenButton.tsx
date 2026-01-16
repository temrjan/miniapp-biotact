import { Maximize2 } from 'lucide-react';
import { useState, useEffect } from 'react';
import { cn } from '@utils/cn';

export function FullscreenButton() {
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isSupported, setIsSupported] = useState(false);
  
  const tgWebApp = (window as any).Telegram?.WebApp;

  useEffect(() => {
    // Check if fullscreen is supported
    setIsSupported(!!tgWebApp?.requestFullscreen);
    setIsFullscreen(!!tgWebApp?.isFullscreen);
    
    // Listen for fullscreen changes
    if (tgWebApp?.onEvent) {
      tgWebApp.onEvent('fullscreenChanged', () => {
        setIsFullscreen(!!tgWebApp.isFullscreen);
      });
    }
  }, []);

  const handleFullscreen = () => {
    if (!tgWebApp) return;
    
    try {
      if (isFullscreen) {
        tgWebApp.exitFullscreen?.();
      } else {
        tgWebApp.requestFullscreen?.();
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
