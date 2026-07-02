import { useState } from 'react';
import { motion } from 'framer-motion';
import { Gift } from 'lucide-react';

import { Card } from '@components/ui/Card';
import { Button } from '@components/ui/Button';
import { useJoin } from '@hooks/useJoin';
import { useTranslation } from '@/i18n';

// Direct-join CTA (no QR): mandatory data-processing consent checkbox gates the "Join" button.
// On success the loyalty summary is invalidated → the parent (LoyaltySection) swaps to the card.
export function JoinCard() {
  const { t } = useTranslation();
  const [agreed, setAgreed] = useState(false);
  const join = useJoin();

  return (
    <motion.section initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}>
      <Card className="p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Gift size={18} className="text-[var(--primary)]" aria-hidden />
          <span className="font-semibold">{t('join.title')}</span>
        </div>
        <p className="text-sm text-[var(--text-muted)]">{t('join.subtitle')}</p>

        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={agreed}
            onChange={(e) => setAgreed(e.target.checked)}
            className="mt-1 shrink-0"
          />
          <span className="text-sm text-[var(--text-secondary)]">{t('activation.consentLabel')}</span>
        </label>

        {join.isError && (
          <p className="text-sm text-[var(--error)]">{t('activation.error.generic')}</p>
        )}

        <Button
          fullWidth
          disabled={!agreed || join.isSuccess}
          isLoading={join.isPending}
          onClick={() => join.mutate()}
        >
          {t('join.button')}
        </Button>
      </Card>
    </motion.section>
  );
}
