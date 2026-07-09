import type { LucideIcon } from 'lucide-react'
import { Card } from '@/shared/components/ui/card'
import { Skeleton } from '@/shared/components/ui/skeleton'

// Thin loyalty-local stat tile. Per-slice card is the project convention
// (dashboard StatsCard + customers StatsCards are both local) — kept local to
// avoid a cross-feature import and without promoting to shared/ui (out of scope).

interface LoyaltyStatCardProps {
  title: string
  value: string | number
  subtitle?: string
  icon?: LucideIcon
  hero?: boolean
  isLoading?: boolean
}

export function LoyaltyStatCard({
  title,
  value,
  subtitle,
  icon: Icon,
  hero = false,
  isLoading = false,
}: LoyaltyStatCardProps) {
  if (isLoading) {
    return (
      <Card className="p-6">
        <div className="flex items-center justify-between">
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-8 w-32" />
            <Skeleton className="h-3 w-20" />
          </div>
          <Skeleton className="w-12 h-12 rounded-lg" />
        </div>
      </Card>
    )
  }

  return (
    <Card className={hero ? 'p-6 border-primary/40 bg-primary/5' : 'p-6'}>
      <div className="flex items-center justify-between">
        <div className="flex-1">
          <p className="text-sm font-medium text-muted-foreground">{title}</p>
          <h3 className={hero ? 'text-4xl font-bold mt-2' : 'text-2xl font-bold mt-2'}>
            {value}
          </h3>
          {subtitle && <p className="text-xs text-muted-foreground mt-1">{subtitle}</p>}
        </div>
        {Icon && (
          <div className="w-12 h-12 bg-primary/10 rounded-lg flex items-center justify-center">
            <Icon className="w-6 h-6 text-primary" />
          </div>
        )}
      </div>
    </Card>
  )
}
