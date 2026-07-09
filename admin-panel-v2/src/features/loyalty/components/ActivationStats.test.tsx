import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { ActivationStats } from './ActivationStats'

describe('ActivationStats', () => {
  it('НИТ-3/Ф5: герой qr_activated подписан как коробки (all-time)', () => {
    const stats = {
      qr_activated: 42,
      qr_generated: 73,
      activation_rate: 57.5,
      batches_count: 24,
      qr_activated_7d: 5,
      qr_activated_30d: 12,
    }
    render(<ActivationStats stats={stats} />)
    expect(screen.getByText('Активировано коробок')).toBeInTheDocument()
    expect(screen.getByText('QR-кодов активировано (всего)')).toBeInTheDocument()
    expect(screen.getByText('42')).toBeInTheDocument()
    expect(screen.getByText('57.5%')).toBeInTheDocument()
  })

  it('пустые данные: activation_rate рендерит 0.0%, счётчики 0', () => {
    const stats = {
      qr_activated: 0,
      qr_generated: 0,
      activation_rate: 0,
      batches_count: 0,
      qr_activated_7d: 0,
      qr_activated_30d: 0,
    }
    render(<ActivationStats stats={stats} />)
    expect(screen.getByText('0.0%')).toBeInTheDocument()
  })
})
