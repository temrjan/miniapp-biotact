import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { MembersStats } from './MembersStats'

describe('MembersStats', () => {
  const stats = { profiles_total: 12, friends: 3, phone_verified: 507 }

  it('рендерит значения участников', () => {
    render(<MembersStats stats={stats} />)
    expect(screen.getByText('12')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('507')).toBeInTheDocument()
  })

  it('НИТ-2: phone_verified несёт квалификатор «по всей базе» (не «участников с телефоном»)', () => {
    render(<MembersStats stats={stats} />)
    expect(screen.getByText('Телефон подтверждён')).toBeInTheDocument()
    expect(screen.getByText('по всей базе')).toBeInTheDocument()
  })
})
