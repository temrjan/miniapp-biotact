import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { TicketsStats } from './TicketsStats'

const stats = (owed: number) => ({
  tickets_total: 12,
  tickets_clean: 9,
  eligible_without_survey_ticket: owed,
})

const renderStats = (owed = 4) =>
  render(
    <MemoryRouter>
      <TicketsStats stats={stats(owed)} />
    </MemoryRouter>
  )

describe('TicketsStats', () => {
  it('turns each number into a link to its own slice of the holders list', () => {
    renderStats()

    const links = screen.getAllByRole('link')
    expect(links.map((link) => link.getAttribute('href'))).toEqual([
      '/loyalty/holders?filter=all',
      '/loyalty/holders?filter=clean',
      '/loyalty/holders?filter=owed',
    ])
  })

  it('keeps the counts on the cards', () => {
    renderStats()

    expect(screen.getByText('12')).toBeInTheDocument()
    expect(screen.getByText('9')).toBeInTheDocument()
    expect(screen.getByText('4')).toBeInTheDocument()
  })

  it('raises the alarm when a member is owed a ticket and does not have one', () => {
    // Issuance may fail silently — it runs inside the money transaction and is allowed to fail
    // without taking the cashback with it, so nothing else would show a skipped ticket.
    // The alarm was briefly demoted on the belief that the number could never be zero (no
    // backfill). Measured on production 2026-08-04: the backfill HAD run on 31.07 and the detector
    // read 0, so the belief was false and the alarm is back. Zero is the healthy invariant.
    renderStats(4)

    expect(screen.getByText('⚠️ Билет не выдан')).toBeInTheDocument()
    expect(screen.getByText('участники ждут билет за анкету')).toBeInTheDocument()
  })

  it('stays calm at zero, which is the healthy state', () => {
    renderStats(0)

    expect(screen.getByText('Пропусков выдачи')).toBeInTheDocument()
    expect(screen.getByText('норма — ноль')).toBeInTheDocument()
    expect(screen.queryByText('⚠️ Билет не выдан')).not.toBeInTheDocument()
  })

  it('keeps the detector clickable in BOTH states', () => {
    // What the demotion left behind and this fix keeps: an empty list is an answer too, so the
    // card leads to it even when nothing is wrong. Before PR #115 the number led nowhere at all.
    const alarmed = render(
      <MemoryRouter>
        <TicketsStats stats={stats(4)} />
      </MemoryRouter>
    )
    expect(
      alarmed.container.querySelector('a[href="/loyalty/holders?filter=owed"]')
    ).not.toBeNull()
    alarmed.unmount()

    const calm = render(
      <MemoryRouter>
        <TicketsStats stats={stats(0)} />
      </MemoryRouter>
    )
    expect(calm.container.querySelector('a[href="/loyalty/holders?filter=owed"]')).not.toBeNull()
  })
})
