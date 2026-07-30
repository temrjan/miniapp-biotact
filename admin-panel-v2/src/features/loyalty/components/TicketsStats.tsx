import { Ticket, TicketCheck, AlertTriangle } from 'lucide-react'
import { LoyaltyStatCard } from './LoyaltyStatCard'
import type { LoyaltyStats } from '../types'

interface TicketsStatsProps {
  stats: Pick<LoyaltyStats, 'tickets_total' | 'tickets_clean' | 'eligible_without_survey_ticket'>
  isLoading?: boolean
}

export function TicketsStats({ stats, isLoading = false }: TicketsStatsProps) {
  // The third card is a detector, not a statistic. Issuance runs inside the activation and survey
  // transactions and is deliberately allowed to fail without taking the cashback down with it — so
  // a skipped issuance raises nothing and leaves the other two counters growing normally. The only
  // thing that shows it is the number of members who are owed a survey ticket and do not have one.
  // It reads 0 in a healthy system; anything else is a defect visible without opening a log.
  const leak = stats.eligible_without_survey_ticket > 0

  return (
    <section>
      <h2 className="text-xl font-semibold mb-4">Билеты розыгрыша</h2>
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <LoyaltyStatCard
          title="Билетов выдано"
          value={stats.tickets_total}
          subtitle="за всё время"
          icon={Ticket}
          isLoading={isLoading}
        />
        <LoyaltyStatCard
          title="На руках"
          value={stats.tickets_clean}
          subtitle="ещё не сыграли"
          icon={TicketCheck}
          isLoading={isLoading}
        />
        <LoyaltyStatCard
          title={leak ? '⚠️ Билет не выдан' : 'Пропусков выдачи'}
          value={stats.eligible_without_survey_ticket}
          subtitle={leak ? 'участники ждут билет за анкету' : 'норма — ноль'}
          icon={AlertTriangle}
          isLoading={isLoading}
        />
      </div>
    </section>
  )
}
