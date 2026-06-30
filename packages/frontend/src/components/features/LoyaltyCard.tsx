import { motion } from 'framer-motion';
import { Award, Star } from 'lucide-react';

import { Card } from '@components/ui/Card';
import { useLoyalty } from '@hooks/useLoyalty';
import { useTranslation } from '@/i18n';

const FRIEND_THRESHOLD = 10;

export function LoyaltyCard() {
  const { data } = useLoyalty();
  const { t } = useTranslation();

  // Hidden while loading, on the dark 404, or on any error — the Profile must keep working.
  if (!data) {
    return null;
  }

  const isFriend = data.tier === 'biotact_friend';
  // NIT-1: a newbie whose counter is already ≥10 (backfill) shows a normal capped
  // progress, never an "almost Друг" claim — the bar fills from lifetime activations.
  const reached = Math.min(data.lifetimeActivations, FRIEND_THRESHOLD);
  const progressPct = (reached / FRIEND_THRESHOLD) * 100;

  return (
    <motion.section
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.03 }}
    >
      <Card className="p-4 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-sm text-[var(--text-muted)]">{t('profile.loyalty.title')}</span>
          <span
            className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-semibold ${
              isFriend
                ? 'bg-[var(--primary)]/15 text-[var(--primary)]'
                : 'bg-[var(--shadow-dark)]/10 text-[var(--text-secondary)]'
            }`}
          >
            {isFriend ? <Award size={13} /> : <Star size={13} />}
            {isFriend ? t('profile.loyalty.friend') : t('profile.loyalty.newbie')}
          </span>
        </div>

        <div className="flex items-baseline gap-1.5">
          <span data-testid="loyalty-balance" className="text-2xl font-bold">
            {data.balance.toLocaleString('ru-RU')}
          </span>
          <span className="text-sm text-[var(--text-muted)]">{t('profile.loyalty.points')}</span>
        </div>

        {isFriend ? (
          <p className="text-sm font-medium text-[var(--primary)]">
            {t('profile.loyalty.friendPerk')}
          </p>
        ) : (
          <div className="space-y-1.5">
            <div className="h-2 rounded-full bg-[var(--shadow-dark)]/10 overflow-hidden">
              <div
                className="h-full rounded-full bg-[var(--primary)] transition-[width]"
                style={{ width: `${progressPct}%` }}
              />
            </div>
            <p className="text-xs text-[var(--text-muted)]">
              {reached} / {FRIEND_THRESHOLD} {t('profile.loyalty.toFriend')}
            </p>
          </div>
        )}
      </Card>
    </motion.section>
  );
}
