import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/components/ui/select'
import { Button } from '@/shared/components/ui/button'
import { useLoyaltyStats } from '../hooks/useLoyalty'
import {
  ActivationStats,
  ActivationTrendChart,
  MembersStats,
  TicketsStats,
  RegistrationFunnel,
  PointsEconomy,
  DemographicsBreakdown,
} from '../components'

const PERIOD_OPTIONS = [
  { value: '7', label: 'За 7 дней' },
  { value: '30', label: 'За 30 дней' },
  { value: '90', label: 'За 90 дней' },
]

export const LoyaltyPage = () => {
  const [days, setDays] = useState(30)
  const { data, isLoading, error } = useLoyaltyStats(days)

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <div className="inline-block h-8 w-8 animate-spin rounded-full border-4 border-solid border-current border-r-transparent" />
          <p className="mt-4 text-sm text-muted-foreground">Загрузка...</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-sm text-destructive">
            Ошибка загрузки данных: {error instanceof Error ? error.message : 'Unknown error'}
          </p>
        </div>
      </div>
    )
  }

  if (!data) {
    return null
  }

  return (
    <div className="p-8 space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Biotact Club</h1>
          <p className="text-muted-foreground mt-1">Мониторинг программы лояльности</p>
        </div>
        <div className="flex items-center gap-3">
          <Button variant="outline" asChild>
            <Link to="/loyalty/draw">Провести розыгрыш</Link>
          </Button>
          <Select value={days.toString()} onValueChange={(value) => setDays(Number(value))}>
            <SelectTrigger className="w-[150px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PERIOD_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Block 1 — QR activations (hero) */}
      <ActivationStats stats={data} />
      <ActivationTrendChart data={data.activations_by_day} days={days} />

      {/* Block 2 — Members */}
      <MembersStats stats={data} />

      {/* Block 3 — Registration funnel */}
      <RegistrationFunnel funnel={data.funnel} consentsEverGranted={data.consents_ever_granted} />

      {/* Block 3b — Lottery tickets */}
      <TicketsStats stats={data} />

      {/* Block 4 — Points economy */}
      <PointsEconomy stats={data} />

      {/* Block 5 — Demographics */}
      <DemographicsBreakdown demographics={data.demographics} profilesTotal={data.profiles_total} />
    </div>
  )
}
