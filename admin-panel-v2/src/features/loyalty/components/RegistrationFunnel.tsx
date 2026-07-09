import { ShieldCheck } from 'lucide-react'
import { Card, CardHeader, CardTitle, CardContent } from '@/shared/components/ui/card'
import { LoyaltyStatCard } from './LoyaltyStatCard'
import type { FunnelStep } from '../types'

interface RegistrationFunnelProps {
  funnel: FunnelStep[]
  consentsEverGranted: number
}

// RU labels for CustomerEventType funnel steps. NIT-3: `qr_activated` here = distinct
// PEOPLE who activated, NOT boxes (that is the hero tile). Fallback to raw value (Ф2).
const FUNNEL_STEP_LABELS: Record<string, string> = {
  joined: 'Вошёл',
  phone_shared: 'Телефон',
  qr_activated: 'Активировали (людей)',
  survey_done: 'Анкета',
}

export function RegistrationFunnel({ funnel, consentsEverGranted }: RegistrationFunnelProps) {
  // Ф1: steps are counted INDEPENDENTLY on the backend (count(distinct user_id) per event
  // type), NOT nested subsets — a later step may exceed an earlier one. Scale each bar to the
  // MAX step (never to the previous one) so a non-monotonic funnel never renders broken, and
  // show NO between-step conversion %.
  const maxUsers = Math.max(0, ...funnel.map((s) => s.users))

  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle className="text-lg">Ступени регистрации</CardTitle>
        </CardHeader>
        <CardContent>
          {funnel.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-8">
              Пока никто не регистрировался
            </p>
          ) : (
            <div className="space-y-4">
              {funnel.map((step) => {
                const label = FUNNEL_STEP_LABELS[step.step] ?? step.step
                const widthPct = maxUsers > 0 ? (step.users / maxUsers) * 100 : 0
                return (
                  <div key={step.step}>
                    <div className="flex items-center justify-between text-sm mb-1">
                      <span className="text-muted-foreground">{label}</span>
                      <span className="font-medium">{step.users}</span>
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
          )}
        </CardContent>
      </Card>

      <LoyaltyStatCard
        title="Дали согласие"
        value={consentsEverGranted}
        subtitle="хоть раз (вкл. отозвавших)"
        icon={ShieldCheck}
      />
    </div>
  )
}
