import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FileText, X } from 'lucide-react';
import { Card } from '@components/ui/Card';
import { useTranslation } from '@/i18n';
import { useHaptic } from '@hooks/useTelegram';
import { buildConsentVersion } from '@/legal';
import { ROUTES } from '@utils/constants';

// localStorage, а не in-memory: «один раз на версию» обязано переживать перезапуск
// мини-аппа (критерий 8). Значение — сама версия, не флаг: бамп редакции меняет
// buildConsentVersion(), сохранённое перестаёт совпадать, баннер появляется снова.
export const DOCS_NOTICE_STORAGE_KEY = 'legal_docs_notice_seen';

/**
 * Одноразовый баннер членам «опубликованы/обновлены документы Программы» (PR-3,
 * оферта п. 13.2: новая редакция вводится через уведомление). Монтируется только
 * под `legalOn && isMember` в ProfilePage — сам компонент членство не проверяет.
 */
export function DocsNoticeBanner() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const haptic = useHaptic();
  const version = buildConsentVersion();
  const [seen, setSeen] = useState(() => localStorage.getItem(DOCS_NOTICE_STORAGE_KEY) === version);

  if (seen) return null;

  const markSeen = () => {
    localStorage.setItem(DOCS_NOTICE_STORAGE_KEY, version);
    setSeen(true);
  };

  const handleOpenDocs = () => {
    haptic.light();
    // Переход к документам тоже считается ознакомлением с уведомлением — иначе
    // член, открывший документы через баннер, увидел бы его снова при возврате.
    markSeen();
    navigate(ROUTES.LEGAL);
  };

  return (
    <Card className="p-4 space-y-3" data-testid="docs-notice-banner">
      <div className="flex items-start gap-4">
        <div className="neumorphic-sm w-10 h-10 rounded-full flex items-center justify-center shrink-0">
          <FileText size={20} className="text-[var(--text-secondary)]" />
        </div>
        <div className="flex-1">
          <span className="font-semibold">{t('profile.docsNoticeTitle')}</span>
          <p className="text-sm text-[var(--text-muted)]">{t('profile.docsNoticeBody')}</p>
        </div>
        <button
          type="button"
          onClick={() => {
            haptic.light();
            markSeen();
          }}
          aria-label={t('profile.docsNoticeDismiss')}
          className="shrink-0 text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
        >
          <X size={18} />
        </button>
      </div>
      <button
        type="button"
        onClick={handleOpenDocs}
        className="text-sm font-medium text-[var(--primary)]"
      >
        {t('profile.docsNoticeCta')}
      </button>
    </Card>
  );
}
