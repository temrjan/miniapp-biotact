import { QrCode, Package, Percent, Layers, CalendarRange } from 'lucide-react'
import { LoyaltyStatCard } from './LoyaltyStatCard'
import type { LoyaltyStats } from '../types'

interface ActivationStatsProps {
  stats: Pick<
    LoyaltyStats,
    | 'qr_activated'
    | 'qr_generated'
    | 'activation_rate'
    | 'batches_count'
    | 'qr_activated_7d'
    | 'qr_activated_30d'
  >
  isLoading?: boolean
}

export function ActivationStats({ stats, isLoading = false }: ActivationStatsProps) {
  return (
    <section>
      <h2 className="text-xl font-semibold mb-4">Активации QR</h2>
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {/* Hero: qr_activated = codes with status='used' = BOXES (all-time), not people */}
        <LoyaltyStatCard
          title="Активировано коробок"
          value={stats.qr_activated}
          subtitle="QR-кодов активировано (всего)"
          icon={QrCode}
          hero
          isLoading={isLoading}
        />
        <LoyaltyStatCard
          title="Сгенерировано"
          value={stats.qr_generated}
          subtitle="QR-кодов выпущено"
          icon={Package}
          isLoading={isLoading}
        />
        <LoyaltyStatCard
          title="Конверсия активаций"
          value={`${stats.activation_rate.toFixed(1)}%`}
          subtitle="активировано / сгенерировано"
          icon={Percent}
          isLoading={isLoading}
        />
        {/* 7d/30d = customer_event counts (events), different unit from all-time hero codes */}
        <LoyaltyStatCard
          title="За 7 дней"
          value={stats.qr_activated_7d}
          subtitle="активаций (события)"
          icon={CalendarRange}
          isLoading={isLoading}
        />
        <LoyaltyStatCard
          title="За 30 дней"
          value={stats.qr_activated_30d}
          subtitle="активаций (события)"
          icon={CalendarRange}
          isLoading={isLoading}
        />
        <LoyaltyStatCard
          title="Партий кодов"
          value={stats.batches_count}
          subtitle="code_batches"
          icon={Layers}
          isLoading={isLoading}
        />
      </div>
    </section>
  )
}
