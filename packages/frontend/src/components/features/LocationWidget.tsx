import { useCallback } from 'react';
import { MapPin, ChevronDown } from 'lucide-react';
import { useLocationStore } from '@/stores/locationStore';
import { useHaptic } from '@/hooks/useTelegram';
import { useTranslation } from '@/i18n';
import { useLanguage } from '@/stores/languageStore';

export function LocationWidget() {
  const address = useLocationStore((state) => state.address);
  const openModal = useLocationStore((state) => state.openModal);
  const haptic = useHaptic();
  const { t } = useTranslation();
  const language = useLanguage();

  const hasAddress = address !== null && address.street !== '';

  // Build display address with localized abbreviations
  const getDisplayAddress = (): string => {
    if (!address || !address.street) {
      return t('checkout.addressHint');
    }

    let display = address.street;
    if (address.house) {
      // ru: "д." / uz: "uy"
      const houseAbbr = language === 'uz' ? 'uy' : 'д.';
      display += ', ' + houseAbbr + ' ' + address.house;
    }
    if (address.apartment) {
      // ru: "кв." / uz: "xon."
      const aptAbbr = language === 'uz' ? 'xon.' : 'кв.';
      display += ', ' + aptAbbr + ' ' + address.apartment;
    }

    return display;
  };

  const handleClick = useCallback(() => {
    haptic.light();
    openModal();
  }, [haptic, openModal]);

  return (
    <button
      onClick={handleClick}
      className="flex items-center gap-2 px-3 py-2 rounded-xl bg-[var(--bg-secondary)] hover:bg-[var(--bg-tertiary)] transition-colors w-full"
    >
      <MapPin
        size={18}
        className={hasAddress ? 'text-[var(--primary)]' : 'text-[var(--text-muted)]'}
      />
      <div className="flex-1 text-left min-w-0">
        <p className="text-[10px] text-[var(--text-muted)] uppercase tracking-wide">
          {t('checkout.delivery')}
        </p>
        <p className={`text-sm font-medium truncate ${hasAddress ? 'text-[var(--text-primary)]' : 'text-[var(--text-muted)]'}`}>
          {getDisplayAddress()}
        </p>
      </div>
      <ChevronDown size={16} className="text-[var(--text-muted)] flex-shrink-0" />
    </button>
  );
}
