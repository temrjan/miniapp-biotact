import { render, screen } from '@testing-library/react'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { LoyaltyPage } from './LoyaltyPage'
import { loyaltyApi } from '../api/loyaltyApi'
import type { LoyaltyStats } from '../types'

vi.mock('../api/loyaltyApi', () => ({
  loyaltyApi: { getLoyaltyStats: vi.fn() },
}))

const zeroStats: LoyaltyStats = {
  qr_generated: 0,
  qr_activated: 0,
  qr_activated_7d: 0,
  qr_activated_30d: 0,
  activation_rate: 0,
  batches_count: 0,
  activations_by_day: [],
  profiles_total: 0,
  phone_verified: 0,
  friends: 0,
  funnel: [],
  consents_ever_granted: 0,
  points_earned: 0,
  points_spent: 0,
  points_outstanding: 0,
  points_by_reason: [],
  demographics: { gender: [], age_buckets: [], top_cities: [], survey_coverage: 0 },
}

function renderWithClient(ui: ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
}

describe('LoyaltyPage', () => {
  beforeEach(() => {
    vi.mocked(loyaltyApi.getLoyaltyStats).mockReset()
  })

  it('smoke: пустой прод (all-zero) рендерится без падения, герой и пустые состояния на месте', async () => {
    vi.mocked(loyaltyApi.getLoyaltyStats).mockResolvedValue(zeroStats)
    renderWithClient(<LoyaltyPage />)

    expect(await screen.findByText('Biotact Club')).toBeInTheDocument()
    expect(screen.getByText('Активировано коробок')).toBeInTheDocument()
    expect(screen.getByText('Пока нет активаций')).toBeInTheDocument()
    expect(screen.getByText('Пока никто не регистрировался')).toBeInTheDocument()
    // малая выборка (N=0) → баннер деанонимизации
    expect(screen.getByRole('alert')).toHaveTextContent(/Малая выборка \(N=0\)/)
  })

  it('error-ветка: показывает сообщение об ошибке', async () => {
    vi.mocked(loyaltyApi.getLoyaltyStats).mockRejectedValue(new Error('boom'))
    renderWithClient(<LoyaltyPage />)

    expect(await screen.findByText(/Ошибка загрузки данных/)).toBeInTheDocument()
  })
})
