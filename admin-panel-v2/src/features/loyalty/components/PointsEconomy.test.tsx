import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { PointsEconomy } from './PointsEconomy'
import type { PointsByReason } from '../types'

describe('PointsEconomy', () => {
  it('Ф2: known reason даёт RU-подпись (cashback → Кэшбэк)', () => {
    const points_by_reason: PointsByReason[] = [
      { reason: 'cashback', total: 3325 },
      { reason: 'welcome', total: 1663 },
      { reason: 'redeem', total: -500 },
    ]
    render(
      <PointsEconomy
        stats={{ points_earned: 4988, points_spent: 500, points_outstanding: 4488, points_by_reason }}
      />,
    )
    expect(screen.getByText('Кэшбэк')).toBeInTheDocument()
    expect(screen.getByText('Welcome-бонус')).toBeInTheDocument()
    expect(screen.getByText('Списание')).toBeInTheDocument()
  })

  it('Ф2: неизвестный reason рендерится сырым (фолбэк)', () => {
    const points_by_reason: PointsByReason[] = [{ reason: '__mystery__', total: 10 }]
    render(
      <PointsEconomy
        stats={{ points_earned: 10, points_spent: 0, points_outstanding: 10, points_by_reason }}
      />,
    )
    expect(screen.getByText('__mystery__')).toBeInTheDocument()
  })

  it('пустой points_by_reason → плейсхолдер, тайлы с нулями', () => {
    render(
      <PointsEconomy
        stats={{ points_earned: 0, points_spent: 0, points_outstanding: 0, points_by_reason: [] }}
      />,
    )
    expect(screen.getByText('Баллы ещё не начислялись')).toBeInTheDocument()
    expect(screen.getAllByText('0').length).toBeGreaterThanOrEqual(3)
  })
})
