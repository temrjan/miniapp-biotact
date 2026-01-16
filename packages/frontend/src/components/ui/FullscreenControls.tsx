import { X, Minimize2 } from 'lucide-react';
import { cn } from '@utils/cn';
import '@/types/telegram.d.ts';

export function FullscreenControls() {
  const tgWebApp = window.Telegram?.WebApp as any;
  
  // Только показываем в полноэкранном режиме
  if (!tgWebApp?.isFullscreen) {
    return null;
  }

  const handleMinimize = () => {
    try {
      tgWebApp?.exitFullscreen?.();
    } catch (e) {
      console.log('Exit fullscreen error:', e);
    }
  };

  const handleClose = () => {
    try {
      tgWebApp?.close();
    } catch (e) {
      console.log('Close error:', e);
    }
  };

  return (
    <div className="fixed top-2 right-2 z-[100] flex gap-2">
      <button
        onClick={handleMinimize}
        className={cn(
          'w-10 h-10 flex items-center justify-center',
          'rounded-full bg-black/20 backdrop-blur-sm',
          'text-white hover:bg-black/30 transition-colors',
          'active:scale-95'
        )}
        aria-label="Свернуть"
      >
        <Minimize2 size={20} />
      </button>
      <button
        onClick={handleClose}
        className={cn(
          'w-10 h-10 flex items-center justify-center',
          'rounded-full bg-black/20 backdrop-blur-sm',
          'text-white hover:bg-black/30 transition-colors',
          'active:scale-95'
        )}
        aria-label="Закрыть"
      >
        <X size={20} />
      </button>
    </div>
  );
}
