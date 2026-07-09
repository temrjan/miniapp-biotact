import { Coins, TrendingUp, Wallet } from 'lucide-react'
import { Card, CardHeader, CardTitle, CardContent } from '@/shared/components/ui/card'
import { LoyaltyStatCard } from './LoyaltyStatCard'
import type { LoyaltyStats, PointsByReason } from '../types'

interface PointsEconomyProps {
  stats: Pick<
    LoyaltyStats,
    'points_earned' | 'points_spent' | 'points_outstanding' | 'points_by_reason'
  >
  isLoading?: boolean
}

// RU labels for LedgerReason (all 10 values). Fallback to raw value (Ф2).
const REASON_LABELS: Record<string, string> = {
  cashback: 'Кэшбэк',
  welcome: 'Welcome-бонус',
  friend_bonus: 'Бонус Друга',
  referral: 'Реферал',
  birthday: 'День рождения',
  review: 'Отзыв',
  subscription: 'Подписка',
  l3: 'L3',
  redeem: 'Списание',
  expiry: 'Сгорание',
}

const formatPoints = (value: number): string => value.toLocaleString('ru-RU')

function ReasonBars({ items }: { items: PointsByReason[] }) {
  if (items.length === 0) {
    return (
      <p className="text-sm text-muted-foreground text-center py-8">Баллы ещё не начислялись</p>
    )
  }
  const maxAbs = Math.max(0, ...items.map((r) => Math.abs(r.total)))
  return (
    <div className="space-y-4">
      {items.map((item) => {
        const label = REASON_LABELS[item.reason] ?? item.reason
        const widthPct = maxAbs > 0 ? (Math.abs(item.total) / maxAbs) * 100 : 0
        const negative = item.total < 0
        return (
          <div key={item.reason}>
            <div className="flex items-center justify-between text-sm mb-1">
              <span className="text-muted-foreground">{label}</span>
              <span className="font-medium">{formatPoints(item.total)}</span>
            </div>
            <div className="h-3 w-full rounded-full bg-muted overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${negative ? 'bg-destructive' : 'bg-primary'}`}
                style={{ width: `${widthPct}%` }}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function PointsEconomy({ stats, isLoading = false }: PointsEconomyProps) {
  return (
    <section className="space-y-4">
      <h2 className="text-xl font-semibold">Экономика баллов</h2>
      <div className="grid gap-4 md:grid-cols-3">
        <LoyaltyStatCard
          title="Начислено"
          value={formatPoints(stats.points_earned)}
          subtitle="всего баллов"
          icon={Coins}
          isLoading={isLoading}
        />
        <LoyaltyStatCard
          title="Потрачено"
          value={formatPoints(stats.points_spent)}
          subtitle="всего баллов"
          icon={TrendingUp}
          isLoading={isLoading}
        />
        <LoyaltyStatCard
          title="На руках"
          value={formatPoints(stats.points_outstanding)}
          subtitle="непогашенное обязательство"
          icon={Wallet}
          isLoading={isLoading}
        />
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Баллы по причинам</CardTitle>
        </CardHeader>
        <CardContent>
          <ReasonBars items={stats.points_by_reason} />
        </CardContent>
      </Card>
    </section>
  )
}
