import { Users, Award, Phone } from 'lucide-react'
import { LoyaltyStatCard } from './LoyaltyStatCard'
import type { LoyaltyStats } from '../types'

interface MembersStatsProps {
  stats: Pick<LoyaltyStats, 'profiles_total' | 'friends' | 'phone_verified'>
  isLoading?: boolean
}

export function MembersStats({ stats, isLoading = false }: MembersStatsProps) {
  return (
    <section>
      <h2 className="text-xl font-semibold mb-4">Участники</h2>
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <LoyaltyStatCard
          title="Участников"
          value={stats.profiles_total}
          subtitle="вошли в лоялти-поток"
          icon={Users}
          isLoading={isLoading}
        />
        <LoyaltyStatCard
          title="Друзей"
          value={stats.friends}
          subtitle="статус Biotact Friend"
          icon={Award}
          isLoading={isLoading}
        />
        {/* NIT-2: phone_verified is GLOBAL — count(users WHERE phone_verified_at IS NOT NULL)
            across the whole base, not loyalty-scoped. Subtitle makes the scope honest. */}
        <LoyaltyStatCard
          title="Телефон подтверждён"
          value={stats.phone_verified}
          subtitle="по всей базе"
          icon={Phone}
          isLoading={isLoading}
        />
      </div>
    </section>
  )
}
