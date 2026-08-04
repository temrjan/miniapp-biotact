import { Ticket, TicketCheck, AlertTriangle } from 'lucide-react'
import { Link } from 'react-router-dom'
import { LoyaltyStatCard } from './LoyaltyStatCard'
import type { HoldersFilter, LoyaltyStats } from '../types'

interface TicketsStatsProps {
  stats: Pick<LoyaltyStats, 'tickets_total' | 'tickets_clean' | 'eligible_without_survey_ticket'>
  isLoading?: boolean
}

/** Each number opens the list behind it — a count alone answers neither "whose" nor "for what". */
const holdersLink = (filter: HoldersFilter): string => `/loyalty/holders?filter=${filter}`

export function TicketsStats({ stats, isLoading = false }: TicketsStatsProps) {
  // Issuance runs inside the activation and survey transactions and is deliberately allowed to fail
  // without taking the cashback down with it — so a skipped issuance raises nothing, and the only
  // thing that shows it is the count of members owed a survey ticket who do not have one.
  //
  // It reads 0 in a healthy system, and it read 0 on production on 2026-08-04: the backfill ran on
  // 31.07 (batch `initial-2026-08`), so every eligible member already holds their ticket and a new
  // one is issued the moment the survey is saved. Anything else is a defect.
  //
  // This alarm was briefly demoted to a reference on the belief that no backfill would run and the
  // number would therefore never be zero. The belief was wrong — measured, not argued — and the
  // demotion is undone. What the demotion DID leave behind is kept: the card is a link, so the
  // question "who exactly" is one click away instead of unanswerable.
  const leak = stats.eligible_without_survey_ticket > 0

  return (
    <section>
      <h2 className="text-xl font-semibold mb-4">Билеты розыгрыша</h2>
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Link to={holdersLink('all')} className="block hover:opacity-80 transition-opacity">
          <LoyaltyStatCard
            title="Билетов выдано"
            value={stats.tickets_total}
            subtitle="за всё время · открыть список"
            icon={Ticket}
            isLoading={isLoading}
          />
        </Link>
        <Link to={holdersLink('clean')} className="block hover:opacity-80 transition-opacity">
          <LoyaltyStatCard
            title="На руках"
            value={stats.tickets_clean}
            subtitle="ещё не сыграли · открыть список"
            icon={TicketCheck}
            isLoading={isLoading}
          />
        </Link>
        <Link to={holdersLink('owed')} className="block hover:opacity-80 transition-opacity">
          <LoyaltyStatCard
            title={leak ? '⚠️ Билет не выдан' : 'Пропусков выдачи'}
            value={stats.eligible_without_survey_ticket}
            subtitle={leak ? 'участники ждут билет за анкету' : 'норма — ноль'}
            icon={AlertTriangle}
            isLoading={isLoading}
          />
        </Link>
      </div>
    </section>
  )
}
