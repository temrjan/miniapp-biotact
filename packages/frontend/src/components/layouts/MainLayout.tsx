import type { ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { cn } from '@utils/cn';
import { usePlatform } from '@hooks/useTelegram';
import { ROUTES } from '@utils/constants';
import { InteractiveMenu } from '@components/ui/InteractiveMenu';

interface MainLayoutProps {
  children: ReactNode;
}

export function MainLayout({ children }: MainLayoutProps) {
  const location = useLocation();
  const platform = usePlatform();

  const isIOS = platform === 'ios';

  // Hide bottom nav on pages with MainButton (checkout, payment success)
  const hideBottomNav =
    location.pathname === ROUTES.CHECKOUT ||
    location.pathname === ROUTES.PAYMENT_SUCCESS;

  return (
    <div className="flex flex-col min-h-screen">
      {/* Branded Header - Apple style */}
      <header
        className={cn(
          'sticky top-0 z-40 bg-[var(--primary)]/95 backdrop-blur-md shadow-sm',
          isIOS && 'safe-area-top'
        )}
      >
        <div className="max-w-4xl mx-auto px-4 py-3 flex items-center justify-center">
          <img
            src="/logo-white.png"
            alt="BIOTACT"
            className="h-8 object-contain"
          />
        </div>
      </header>

      {/* Main Content */}
      <main
        className={cn(
          'flex-1 overflow-y-auto',
          hideBottomNav ? 'pb-4' : 'pb-20'
        )}
      >
        {children}
      </main>

      {/* Bottom Navigation - hidden on action pages */}
      {!hideBottomNav && (
        <nav
          className={cn(
            'fixed bottom-0 left-0 right-0 z-50',
            'bg-[var(--bg-primary)]',
            isIOS && 'safe-area-bottom'
          )}
        >
          <div className="max-w-4xl mx-auto">
            <InteractiveMenu />
          </div>
        </nav>
      )}

    </div>
  );
}
