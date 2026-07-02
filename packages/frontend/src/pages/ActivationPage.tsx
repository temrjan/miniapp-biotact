import { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { AlertCircle, CheckCircle2, Sparkles } from 'lucide-react';

import { ApiError } from '@/api';
import { Button } from '@components/ui/Button';
import { Card } from '@components/ui/Card';
import { useActivation } from '@hooks/useActivation';
import { useTranslation } from '@/i18n';
import { ROUTES } from '@utils/constants';

type ActivationErrorKey =
  | 'activation.error.dark'
  | 'activation.error.invalidCode'
  | 'activation.error.unavailable'
  | 'activation.error.generic';

// Maps a failed activation to a neutral, non-leaking message. 404 = dark program (not probeable),
// 409 = code invalid / used by someone else, 503 = temporarily unpriced. Only transient failures
// (503 / network / timeout) offer a retry — a 404/409 will not change on repeat.
function errorContent(status: number | undefined): { messageKey: ActivationErrorKey; canRetry: boolean } {
  switch (status) {
    case 404:
      return { messageKey: 'activation.error.dark', canRetry: false };
    case 409:
      return { messageKey: 'activation.error.invalidCode', canRetry: false };
    case 503:
      return { messageKey: 'activation.error.unavailable', canRetry: true };
    // Network/timeout/5xx — a retry can help. A 401 (stale initData) also lands here; its retry is
    // harmless but unhelpful (initData refreshes only on reopen) — acceptable, and 401 is unreachable
    // while the program is dark (the 404 gate fires before auth). Revisit session-expired UX at PR-4.5.
    default:
      return { messageKey: 'activation.error.generic', canRetry: true };
  }
}

export function ActivationPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const [agreed, setAgreed] = useState(false);
  const activation = useActivation();

  const code = searchParams.get('code');

  // No code in the URL — this screen is only reachable via a QR launch (PR-4.4a covers no-code).
  if (!code) {
    return (
      <section className="p-4 space-y-4 text-center">
        <p className="text-[var(--text-secondary)]">{t('activation.noCode')}</p>
        <Button variant="ghost" fullWidth onClick={() => navigate(ROUTES.HOME)}>
          {t('activation.error.toHome')}
        </Button>
      </section>
    );
  }

  // Success — two legal outcomes distinguished by status (never conflate replay with a fresh earn).
  if (activation.isSuccess) {
    const result = activation.data;
    const balance = result.balance.toLocaleString('ru-RU');

    if (result.status === 'already_activated') {
      return (
        <section className="p-4 space-y-4 text-center">
          <CheckCircle2 className="mx-auto text-[var(--primary)]" size={48} aria-hidden />
          <h1 className="text-xl font-bold">{t('activation.replay.title')}</h1>
          <p className="text-[var(--text-secondary)]">{t('activation.replay.message')}</p>
          <p className="text-sm text-[var(--text-muted)]">
            {t('activation.result.balance', { balance })}
          </p>
          <Button fullWidth onClick={() => navigate(ROUTES.PROFILE)}>
            {t('activation.result.toProfile')}
          </Button>
        </section>
      );
    }

    const isFriendFlip = result.tier === 'biotact_friend' && result.friendBonus > 0;
    return (
      <section className="p-4 space-y-3 text-center">
        <Sparkles className="mx-auto text-[var(--primary)]" size={48} aria-hidden />
        <h1 className="text-xl font-bold">{t('activation.result.successTitle')}</h1>
        <p className="text-lg font-semibold text-[var(--primary)]">
          {t('activation.result.earned', { points: result.pointsAwarded.toLocaleString('ru-RU') })}
        </p>
        {result.welcomeBonus > 0 && (
          <p className="text-sm text-[var(--text-secondary)]">
            {t('activation.result.welcome', { points: result.welcomeBonus.toLocaleString('ru-RU') })}
          </p>
        )}
        {isFriendFlip && (
          <p className="text-sm font-medium text-[var(--primary)]">
            {t('activation.result.friendFlip', { bonus: result.friendBonus.toLocaleString('ru-RU') })}
          </p>
        )}
        <p className="text-sm text-[var(--text-muted)]">
          {t('activation.result.balance', { balance })}
        </p>
        <Button fullWidth onClick={() => navigate(ROUTES.PROFILE)}>
          {t('activation.result.toProfile')}
        </Button>
      </section>
    );
  }

  // Error — neutral message + retry only for transient failures.
  if (activation.isError) {
    const status = activation.error instanceof ApiError ? activation.error.status : undefined;
    const { messageKey, canRetry } = errorContent(status);
    return (
      <section className="p-4 space-y-4 text-center">
        <AlertCircle className="mx-auto text-[var(--text-muted)]" size={48} aria-hidden />
        <p className="text-[var(--text-secondary)]">{t(messageKey)}</p>
        {canRetry && (
          <Button fullWidth isLoading={activation.isPending} onClick={() => activation.mutate(code)}>
            {t('activation.error.retry')}
          </Button>
        )}
        <Button variant="ghost" fullWidth onClick={() => navigate(ROUTES.HOME)}>
          {t('activation.error.toHome')}
        </Button>
      </section>
    );
  }

  // Idle — consent gate. "Активировать" is disabled until the data-processing box is checked.
  return (
    <section className="p-4 space-y-4">
      <header className="space-y-1">
        <h1 className="text-xl font-bold">{t('activation.title')}</h1>
        <p className="text-sm text-[var(--text-muted)]">{t('activation.subtitle')}</p>
      </header>
      <Card className="p-4 space-y-4">
        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
            className="mt-1 shrink-0"
          />
          <span className="text-sm text-[var(--text-secondary)]">{t('activation.consentLabel')}</span>
        </label>
        <Button
          fullWidth
          disabled={!agreed}
          isLoading={activation.isPending}
          onClick={() => activation.mutate(code)}
        >
          {t('activation.activateButton')}
        </Button>
      </Card>
    </section>
  );
}
