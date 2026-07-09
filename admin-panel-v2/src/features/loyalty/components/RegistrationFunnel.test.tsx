import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { RegistrationFunnel } from './RegistrationFunnel'
import type { FunnelStep } from '../types'

describe('RegistrationFunnel', () => {
  it('НИТ-3: ступень qr_activated подписана «Активировали (людей)», а не «коробки»', () => {
    const funnel: FunnelStep[] = [
      { step: 'joined', users: 4 },
      { step: 'qr_activated', users: 2 },
    ]
    render(<RegistrationFunnel funnel={funnel} consentsEverGranted={0} />)
    expect(screen.getByText('Активировали (людей)')).toBeInTheDocument()
    // герой-подпись про коробки не должна появляться в воронке
    expect(screen.queryByText(/коробок/i)).not.toBeInTheDocument()
  })

  it('Ф1: немонотонная воронка (qr_activated > joined) рендерится без слома, бары к MAX ступени', () => {
    const funnel: FunnelStep[] = [
      { step: 'joined', users: 2 },
      { step: 'qr_activated', users: 5 },
    ]
    const { container } = render(<RegistrationFunnel funnel={funnel} consentsEverGranted={0} />)
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getByText('5')).toBeInTheDocument()

    const bars = Array.from(container.querySelectorAll<HTMLDivElement>('.bg-primary'))
    expect(bars).toHaveLength(2)
    // масштаб к max(=5): joined 2/5=40%, qr_activated 5/5=100% — НЕ к предыдущей ступени
    expect(bars[0]!).toHaveStyle({ width: '40%' })
    expect(bars[1]!).toHaveStyle({ width: '100%' })
    // никаких отрицательных ширин
    for (const bar of bars) {
      expect(bar.style.width.startsWith('-')).toBe(false)
    }
  })

  it('Ф2: неизвестный step рендерится сырым значением (фолбэк)', () => {
    const funnel: FunnelStep[] = [{ step: '__unknown_step__', users: 1 }]
    render(<RegistrationFunnel funnel={funnel} consentsEverGranted={0} />)
    expect(screen.getByText('__unknown_step__')).toBeInTheDocument()
  })

  it('пустая воронка → плейсхолдер, без баров', () => {
    const { container } = render(<RegistrationFunnel funnel={[]} consentsEverGranted={0} />)
    expect(screen.getByText('Пока никто не регистрировался')).toBeInTheDocument()
    expect(container.querySelectorAll('.bg-primary')).toHaveLength(0)
  })

  it('показывает consents_ever_granted честной подписью', () => {
    render(<RegistrationFunnel funnel={[]} consentsEverGranted={7} />)
    expect(screen.getByText('7')).toBeInTheDocument()
    expect(screen.getByText(/вкл\. отозвавших/i)).toBeInTheDocument()
  })
})
