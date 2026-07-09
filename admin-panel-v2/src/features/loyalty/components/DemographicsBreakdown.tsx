import { AlertTriangle } from 'lucide-react'
import { Card, CardHeader, CardTitle, CardContent } from '@/shared/components/ui/card'
import type { Demographics } from '../types'

interface DemographicsBreakdownProps {
  demographics: Demographics
  profilesTotal: number
}

// Below this many profiles, marginal demographics can point at specific people.
// Admin-only + marginal-only API → we warn (over the whole section), not hard-hide (НИТ-4 / Ф3).
export const SMALL_SAMPLE_THRESHOLD = 10

const GENDER_LABELS: Record<string, string> = {
  male: 'Муж.',
  female: 'Жен.',
  unknown: 'Не указан',
}

const BUCKET_LABELS: Record<string, string> = {
  unknown: 'Не указан',
}

interface LabelledCount {
  label: string
  value: number
}

function CountBars({ items, emptyText }: { items: LabelledCount[]; emptyText: string }) {
  if (items.length === 0) {
    return <p className="text-sm text-muted-foreground text-center py-6">{emptyText}</p>
  }
  const maxValue = Math.max(0, ...items.map((i) => i.value))
  return (
    <div className="space-y-3">
      {items.map((item) => {
        const widthPct = maxValue > 0 ? (item.value / maxValue) * 100 : 0
        return (
          <div key={item.label}>
            <div className="flex items-center justify-between text-sm mb-1">
              <span className="text-muted-foreground">{item.label}</span>
              <span className="font-medium">{item.value}</span>
            </div>
            <div className="h-3 w-full rounded-full bg-muted overflow-hidden">
              <div
                className="h-full rounded-full bg-primary transition-all"
                style={{ width: `${widthPct}%` }}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function DemographicsBreakdown({ demographics, profilesTotal }: DemographicsBreakdownProps) {
  const { gender, age_buckets, top_cities, survey_coverage } = demographics
  const smallSample = profilesTotal < SMALL_SAMPLE_THRESHOLD

  const genderItems: LabelledCount[] = gender.map((g) => ({
    label: GENDER_LABELS[g.gender] ?? g.gender,
    value: g.count,
  }))
  const ageItems: LabelledCount[] = age_buckets.map((a) => ({
    label: BUCKET_LABELS[a.bucket] ?? a.bucket,
    value: a.count,
  }))

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold">Демография</h2>
        <span className="text-sm text-muted-foreground">
          Покрытие анкеты: {survey_coverage.toFixed(1)}%
        </span>
      </div>

      {smallSample && (
        <div
          role="alert"
          className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-700 dark:text-amber-400"
        >
          <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>
            Малая выборка (N={profilesTotal}): значения могут относиться к конкретным участникам.
          </span>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Пол</CardTitle>
          </CardHeader>
          <CardContent>
            <CountBars items={genderItems} emptyText="Нет данных анкеты" />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Возраст</CardTitle>
          </CardHeader>
          <CardContent>
            <CountBars items={ageItems} emptyText="Нет данных анкеты" />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Города</CardTitle>
          </CardHeader>
          <CardContent>
            {top_cities.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-6">Нет данных анкеты</p>
            ) : (
              <ul className="space-y-2">
                {top_cities.map((c) => (
                  <li key={c.city} className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">{c.city}</span>
                    <span className="font-medium">{c.count}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>
    </section>
  )
}
