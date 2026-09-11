import { useState } from 'react';
import { Link } from 'react-router-dom';
import { motion } from 'framer-motion';
import { Gift } from 'lucide-react';

import { Card } from '@components/ui/Card';
import { Button } from '@components/ui/Button';
import { useJoin } from '@hooks/useJoin';
import { useTranslation } from '@/i18n';
import { isLegalEnabled } from '@/legal/flag';
import { useConsentStore } from '@/stores/consentStore';
import { ROUTES } from '@utils/constants';

// Direct-join CTA (no QR): mandatory data-processing consent checkbox gates the "Join" button.
// On success the loyalty summary is invalidated → the parent (LoyaltySection) swaps to the card.
//
// Flag ON (VITE_LEGAL_DOCS): два чекбокса — обязательный (оферта+политика+ПД со ссылками) и
// опциональный маркетинг; галочки живут в consentStore, чтобы пережить переход на документ
// (Finding 2). Flag OFF: прежний одиночный чекбокс, поведение без изменений.
export function JoinCard() {
  const { t } = useTranslation();
  const legalOn = isLegalEnabled();
  const [agreed, setAgreed] = useState(false);
  const { dataProcessing, marketing, setDataProcessing, setMarketing } = useConsentStore();
  const join = useJoin();

  return (
    <motion.section initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
      <Card className="p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Gift size={18} className="text-[var(--primary)]" aria-hidden />
          <span className="font-semibold">{t('join.title')}</span>
        </div>
        <p className="text-sm text-[var(--text-muted)]">{t('join.subtitle')}</p>

        {legalOn ? (
          <>
            <label className="flex items-start gap-3 cursor-pointer">
              <input
                type="checkbox"
                checked={dataProcessing}
                onChange={(e) => setDataProcessing(e.target.checked)}
                className="mt-1 shrink-0"
              />
              <span className="text-sm text-[var(--text-secondary)]">
                {t('activation.consentRequiredBefore')}{' '}
                <Link to={`${ROUTES.LEGAL}/offer`} className="text-[var(--primary)] underline">
                  {t('activation.consentOfferLink')}
                </Link>{' '}
                {t('activation.consentRequiredMiddle')}{' '}
                <Link to={`${ROUTES.LEGAL}/policy`} className="text-[var(--primary)] underline">
                  {t('activation.consentPolicyLink')}
                </Link>{' '}
                {t('activation.consentRequiredAfter')}
              </span>
            </label>
            <label className="flex items-start gap-3 cursor-pointer">
              <input
                type="checkbox"
                checked={marketing}
                onChange={(e) => setMarketing(e.target.checked)}
                className="mt-1 shrink-0"
              />
              <span className="text-sm text-[var(--text-secondary)]">
                {t('activation.consentMarketing')}
              </span>
            </label>
          </>
        ) : (
          <label className="flex items-start gap-3 cursor-pointer">
            <input
              type="checkbox"
              checked={agreed}
              onChange={(e) => setAgreed(e.target.checked)}
              className="mt-1 shrink-0"
            />
            <span className="text-sm text-[var(--text-secondary)]">
              {t('activation.consentLabel')}
            </span>
          </label>
        )}

        {join.isError && (
          <p className="text-sm text-[var(--error)]">{t('activation.error.generic')}</p>
        )}

        <Button
          fullWidth
          disabled={(legalOn ? !dataProcessing : !agreed) || join.isSuccess}
          isLoading={join.isPending}
          onClick={() => join.mutate({ marketing: legalOn ? marketing : false })}
        >
          {t('join.button')}
        </Button>
      </Card>
    </motion.section>
  );
}
