import { motion } from 'framer-motion';
import { Gift, Ticket, Trophy } from 'lucide-react';

import { Card } from '@components/ui/Card';
import { useMyTickets } from '@hooks/useMyTickets';
import { useTranslation } from '@/i18n';

/** Russian needs three forms after a number; Uzbek needs none, and its three keys hold the same
 * word — so the rule is applied without asking which language is on screen. */
function pluralKey(count: number): 'one' | 'few' | 'many' {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return 'one';
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return 'few';
  return 'many';
}

/** Which sentence explains that held tickets will not play. The server sends a CODE, never a
 * sentence — it has no idea which language this member reads. An unknown code degrades to the
 * general line instead of printing a raw identifier or staying silent. */
function blockedKey(reason: string): string {
  if (reason === 'survey') return 'profile.tickets.blockedSurvey';
  if (reason === 'age') return 'profile.tickets.blockedAge';
  return 'profile.tickets.blockedOther';
}

export function TicketsCard() {
  const { data } = useMyTickets();
  const { t } = useTranslation();

  // Hidden while loading, on the dark 404, or on any error — the Profile must keep working.
  if (!data) {
    return null;
  }

  // `tickets` is deliberately NOT destructured: the legacy field still ships for bundles that have
  // not updated yet, but this card reads the two numbers it actually means — stamped and in hand.
  const { isFrozen, period, prizeLabel, result, numbers, cleanTickets, notPlayingReason } = data;

  const unit = (count: number) => t(`profile.tickets.unit.${pluralKey(count)}`);

  // Two different things a member can hold, and the card must not confuse them: tickets STAMPED
  // into the draw on screen, and tickets in hand waiting for the next freeze. Someone who joined
  // after the freeze has zero of the first and may have two of the second — reading `tickets`
  // alone told them "you have none, go earn one" while their two tickets sat right there.
  const stamped = numbers.length;
  const shownCount = stamped > 0 ? stamped : cleanTickets;

  // A stamped stake is decided; tickets in hand are waiting. Nothing expires on its own any more —
  // the 30-day window went away with the old model — so the live wording no longer warns about a
  // number that might shrink.
  const state =
    stamped > 0 && isFrozen
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

        {shownCount > 0 ? (
          <>
            <div className="flex items-baseline gap-1.5">
              <span data-testid="tickets-count" className="text-2xl font-bold">
                {shownCount}
              </span>
              <span data-testid="tickets-unit" className="text-sm text-[var(--text-muted)]">
                {unit(shownCount)}
              </span>
            </div>

            {/* The member's own copy of the published list. Numbers exist only once a draw has
                stamped them, and they STAY after it is drawn: that is when the list is published,
                and finding yourself in it is the entire reason they are shown. */}
            {numbers.length > 0 && (
              <p data-testid="tickets-numbers" className="text-sm font-medium">
                {t('profile.tickets.numbers', { numbers: numbers.join(', ') })}
              </p>
            )}

            <p data-testid="tickets-state" className="text-xs text-[var(--text-muted)]">
              {state}
            </p>
          </>
        ) : (
          <p data-testid="tickets-howto" className="text-sm text-[var(--text-secondary)]">
            {t('profile.tickets.howTo')}
          </p>
        )}

        {/* Tickets in hand while a draw is running: either they wait for the next freeze, or they
            will not play at all — and then the reason REPLACES the promise. Telling someone whose
            survey is incomplete that "one more waits for the next draw" is a quiet refusal dressed
            as good news: without the survey it will not play then either. */}
        {cleanTickets > 0 && stamped > 0 && (
          <p data-testid="tickets-waiting" className="text-xs text-[var(--text-muted)]">
            {notPlayingReason
              ? t(blockedKey(notPlayingReason))
              : t('profile.tickets.waiting', { count: cleanTickets, unit: unit(cleanTickets) })}
          </p>
        )}
        {cleanTickets > 0 && stamped === 0 && notPlayingReason && (
          <p data-testid="tickets-blocked" className="text-sm text-[var(--text-secondary)]">
            {t(blockedKey(notPlayingReason))}
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
