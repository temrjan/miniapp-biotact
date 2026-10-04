import { useLayoutEffect, useRef, useSyncExternalStore } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useLanguage } from '@/stores/languageStore';
import { REFERRAL_KEY } from './context';
import { cleanEntryURL, consumeBootstrap, referralContext } from './runtime';

let documentStarted = false;
export function ReferralOwner() {
  const location = useLocation();
  const navigate = useNavigate();
  const currentLocationKey = useRef(location.key);
  const anchor = useRef<{ key: string; location: string; cleaning: boolean } | null>(null);
  const status = useSyncExternalStore(referralContext.subscribe, referralContext.snapshot);
  const language = useLanguage();

  useLayoutEffect(() => {
    currentLocationKey.current = location.key;
    if (anchor.current?.cleaning) {
      const current = location.pathname + location.search + location.hash;
      if (current === anchor.current.location) {
        anchor.current.key = location.key;
        anchor.current.cleaning = false;
      }
    }
    const bootstrap = consumeBootstrap();
    const entry = cleanEntryURL() ?? bootstrap;
    if (entry) {
      documentStarted = true;
      const origin = {
        key: location.key,
        location: entry.location,
        cleaning: location.search.includes('ref'),
      };
      anchor.current = origin;
      // Synchronize Router's location after the early native history cleanup.
      if (location.search.includes('ref')) navigate(entry.location, { replace: true });
      void referralContext.enter(entry.values, (product) => {
        const current = window.location.pathname + window.location.search + window.location.hash;
        if (
          anchor.current === origin &&
          currentLocationKey.current === origin.key &&
          current === entry.location
        ) {
          navigate(`/product/${product}${window.location.search}${window.location.hash}`, {
            replace: true,
          });
        }
      });
    } else if (!documentStarted) {
      documentStarted = true;
      void referralContext.reopen();
    } else referralContext.checkIdentity();
  }, [location.key, location.pathname, location.search, location.hash, navigate]);

  useLayoutEffect(() => {
    const resume = () => {
      if (document.visibilityState === 'visible') void referralContext.reopen();
    };
    const storage = (event: StorageEvent) => {
      if (event.key === REFERRAL_KEY || event.key === null) void referralContext.storageChanged();
    };
    const reset = () => referralContext.reset();
    window.addEventListener('focus', resume);
    document.addEventListener('visibilitychange', resume);
    window.addEventListener('storage', storage);
    window.addEventListener('biotact-auth-reset', reset);
    return () => {
      window.removeEventListener('focus', resume);
      document.removeEventListener('visibilitychange', resume);
      window.removeEventListener('storage', storage);
      window.removeEventListener('biotact-auth-reset', reset);
    };
  }, []);

  if (status !== 'resolving' && status !== 'unavailable') return null;
  const pending = status === 'resolving';
  const message =
    language === 'uz'
      ? pending
        ? 'Havola tekshirilmoqda'
        : 'Havola hozir mavjud emas. Oddiy buyurtma berishingiz mumkin'
      : pending
        ? 'Проверяем ссылку'
        : 'Ссылка сейчас недоступна. Можно оформить обычный заказ';
  return (
    <p role="status" className="px-4 pt-2 text-sm text-[var(--text-secondary)]">
      {message}
    </p>
  );
}
