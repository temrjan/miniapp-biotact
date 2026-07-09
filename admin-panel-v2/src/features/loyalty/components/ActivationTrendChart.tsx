import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from 'recharts'
import { Card } from '@/shared/components/ui/card'
import { Skeleton } from '@/shared/components/ui/skeleton'
import type { ActivationDataPoint } from '../types'

interface ActivationTrendChartProps {
  data: ActivationDataPoint[]
  days: number
  isLoading?: boolean
}

export function ActivationTrendChart({ data, days, isLoading = false }: ActivationTrendChartProps) {
  // Title carries the active window: `days` scopes ONLY this chart (not the rest of the page).
  const title = `Активации за ${days} дней`

  if (isLoading) {
    return (
      <Card className="p-6">
        <Skeleton className="h-6 w-48 mb-4" />
        <Skeleton className="h-[300px] w-full" />
      </Card>
    )
  }

  if (data.length === 0) {
    return (
      <Card className="p-6">
        <h3 className="text-lg font-semibold mb-4">{title}</h3>
        <p className="text-sm text-muted-foreground text-center py-12">Пока нет активаций</p>
      </Card>
    )
  }

  const chartData = data.map((point) => ({
    date: new Date(point.date).toLocaleDateString('ru-RU', { month: 'short', day: 'numeric' }),
    count: point.count,
  }))

  return (
    <Card className="p-6">
      <h3 className="text-lg font-semibold mb-4">{title}</h3>
      <ResponsiveContainer width="100%" height={300}>
        <AreaChart data={chartData}>
          <defs>
            <linearGradient id="colorActivations" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="hsl(var(--primary))" stopOpacity={0.3} />
              <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 12 }}
            interval="preserveStartEnd"
            className="text-muted-foreground"
          />
          <YAxis tick={{ fontSize: 12 }} allowDecimals={false} className="text-muted-foreground" />
          <Tooltip
            formatter={(value: number | undefined) => [value ?? 0, 'Активаций']}
            labelStyle={{ color: 'hsl(var(--foreground))' }}
            contentStyle={{
              backgroundColor: 'hsl(var(--background))',
              border: '1px solid hsl(var(--border))',
              borderRadius: '8px',
            }}
          />
          <Area
            type="monotone"
            dataKey="count"
            stroke="hsl(var(--primary))"
            strokeWidth={2}
            fill="url(#colorActivations)"
            animationDuration={1000}
          />
        </AreaChart>
      </ResponsiveContainer>
    </Card>
  )
}
