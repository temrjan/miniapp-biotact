import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { TicketsStats } from './TicketsStats'

const stats = {
  tickets_total: 12,
  tickets_clean: 9,
  eligible_without_survey_ticket: 4,
}

const renderStats = () =>
  render(
    <MemoryRouter>
      <TicketsStats stats={stats} />
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

  it('states the waiting members as a reference, not as a defect', () => {
    // Since the 2026-08-04 decision not to backfill, a non-zero value here is the EXPECTED state:
    // members who filled the survey before issuance existed hold no ticket until they act again.
    // The old wording called any non-zero a defect and lit a warning — permanent, therefore
    // ignored, and the genuine signal would have died with it. It now lives in the list's ordering.
    renderStats()

    expect(screen.getByText('Ждут билет за анкету')).toBeInTheDocument()
    expect(screen.getByText(/получат при следующем действии/)).toBeInTheDocument()
    expect(screen.queryByText(/Билет не выдан/)).not.toBeInTheDocument()
    expect(screen.queryByText(/норма — ноль/)).not.toBeInTheDocument()
  })
})
