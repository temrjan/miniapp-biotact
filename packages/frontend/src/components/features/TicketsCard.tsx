import { motion } from 'framer-motion';
import { Gift, Ticket, Trophy } from 'lucide-react';

import { Card } from '@components/ui/Card';
import { useMyTickets } from '@hooks/useMyTickets';
import { useTranslation } from '@/i18n';

export function TicketsCard() {
  const { data } = useMyTickets();
  const { t } = useTranslation();

  // Hidden while loading, on the dark 404, or on any error — the Profile must keep working.
  if (!data) {
    return null;
  }

  const { tickets, isFrozen, period, prizeLabel, result } = data;

  // The same number is worded differently on purpose. A frozen count cannot change any more; a live
  // one still can (an activation ages out of the 30-day window). Saying "you have 2" in both cases
  // would turn a legitimate recount into what looks like tickets being taken away.
  const state = isFrozen
    ? t('profile.tickets.frozen', { period: period ?? '' })
    : t('profile.tickets.prospective');

  return (
    <motion.section
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: 0.04 }}
    >
      <Card className="p-4 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-sm text-[var(--text-muted)]">{t('profile.tickets.title')}</span>
          <Ticket size={16} className="text-[var(--primary)]" />
        </div>

        {tickets > 0 ? (
          <>
            <div className="flex items-baseline gap-1.5">
              <span data-testid="tickets-count" className="text-2xl font-bold">
                {tickets}
              </span>
              <span className="text-sm text-[var(--text-muted)]">
                {t('profile.tickets.unit')}
              </span>
            </div>
            <p data-testid="tickets-state" className="text-xs text-[var(--text-muted)]">
              {state}
            </p>
          </>
        ) : (
          <p data-testid="tickets-howto" className="text-sm text-[var(--text-secondary)]">
            {t('profile.tickets.howTo')}
          </p>
        )}

        {prizeLabel && (
          <p className="inline-flex items-center gap-1.5 text-sm font-medium text-[var(--primary)]">
            <Gift size={14} />
            {t('profile.tickets.prize', { prize: prizeLabel })}
          </p>
        )}

        {/* The finished draw is a separate block: it describes a DIFFERENT draw than the count
            above, which already counts towards the next one. Merging them would either hide the
            outcome or claim the member is still in a draw that is over. */}
        {result && (
          <div
            data-testid="tickets-result"
            className="pt-2 border-t border-[var(--shadow-dark)]/10 space-y-0.5"
          >
            <p className="inline-flex items-center gap-1.5 text-sm font-medium">
              {result.won && <Trophy size={14} className="text-[var(--primary)]" />}
              {result.won ? t('profile.tickets.won') : t('profile.tickets.lost')}
            </p>
            {/* What they won, and only when they won. The draw's prize belongs beside a victory;
                printing it under a loss would read as if this member had received it. */}
            {result.won && result.prizeRank !== null && (
              <p className="text-sm font-medium text-[var(--primary)]">
                {t('profile.tickets.rank', { rank: result.prizeRank })}
                {result.prizeLabel ? ` · ${result.prizeLabel}` : ''}
              </p>
            )}
            <p className="text-xs text-[var(--text-muted)]">
              {t('profile.tickets.resultPeriod', { period: result.period })}
            </p>
          </div>
        )}
      </Card>
    </motion.section>
  );
}
