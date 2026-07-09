import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { QrCode } from 'lucide-react'
import { LoyaltyStatCard } from './LoyaltyStatCard'

describe('LoyaltyStatCard', () => {
  it('рендерит title и value', () => {
    render(<LoyaltyStatCard title="Коробки" value={42} />)
    expect(screen.getByText('Коробки')).toBeInTheDocument()
    expect(screen.getByText('42')).toBeInTheDocument()
  })

  it('рендерит subtitle когда передан', () => {
    render(<LoyaltyStatCard title="Коробки" value={42} subtitle="всего" />)
    expect(screen.getByText('всего')).toBeInTheDocument()
  })

  it('скелет при isLoading — значение скрыто', () => {
    render(<LoyaltyStatCard title="Коробки" value={42} icon={QrCode} isLoading />)
    expect(screen.queryByText('42')).not.toBeInTheDocument()
    expect(screen.queryByText('Коробки')).not.toBeInTheDocument()
  })
})
