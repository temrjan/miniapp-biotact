import { Ticket, TicketCheck, Hourglass } from 'lucide-react'
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
        {/*
          Reference, not an alarm — and the change is deliberate.
          Until 2026-08-04 this card read "0 is the norm, anything else is a defect": issuance runs
          inside the activation and survey transactions and is allowed to fail without taking the
          cashback down with it, so a skipped issuance raises nothing and only this count would show
          it. Then the Captain decided NOT to backfill tickets, which makes a non-zero value the
          expected state — members who filled the survey before issuance existed hold no ticket
          until their next action. A siren that rings permanently is taped over, and the real signal
          would die with it.
          The signal did not disappear: it moved into the LIST, ordered by last activation. A member
          who activated a QR recently and still holds no survey ticket is a genuine failure — the
          activation path issues every source. That is why this card links to `owed` rather than
          shouting a colour nobody can act on.
        */}
        <Link to={holdersLink('owed')} className="block hover:opacity-80 transition-opacity">
          <LoyaltyStatCard
            title="Ждут билет за анкету"
            value={stats.eligible_without_survey_ticket}
            subtitle="получат при следующем действии · открыть список"
            icon={Hourglass}
            isLoading={isLoading}
          />
        </Link>
      </div>
    </section>
  )
}
