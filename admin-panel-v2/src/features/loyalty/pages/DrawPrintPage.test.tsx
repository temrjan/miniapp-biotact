import { render, screen } from '@testing-library/react'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { DrawPrintPage } from './DrawPrintPage'
import { loyaltyApi } from '../api/loyaltyApi'

vi.mock('../api/loyaltyApi', () => ({
  loyaltyApi: { getPrintList: vi.fn() },
}))

function renderPrintPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/loyalty/draw/print/5']}>
        <Routes>
          <Route path="/loyalty/draw/print/:drawId" element={<DrawPrintPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  )
}

describe('DrawPrintPage', () => {
  beforeEach(() => {
    vi.mocked(loyaltyApi.getPrintList).mockReset()
  })

  it('renders the list with numbers, padded last-4, and the total count as text', async () => {
    vi.mocked(loyaltyApi.getPrintList).mockResolvedValue({
      draw_number: 3,
      conducted_on: null,
      total_tickets: 2,
      rows: [
        { ticket_number: 1, telegram_last4: '0042' },
        { ticket_number: 2, telegram_last4: '7731' },
      ],
    })
    renderPrintPage()

    expect(await screen.findByText('Розыгрыш №3 — список билетов')).toBeInTheDocument()
    expect(screen.getByText('0042')).toBeInTheDocument()
    expect(screen.getByText('7731')).toBeInTheDocument()
    // the range of the draw itself, as text — the point of the list
    expect(screen.getByText('Всего билетов: 2')).toBeInTheDocument()
    // no date yet → honest placeholder, not a blank
    expect(screen.getByText('Дата уточняется')).toBeInTheDocument()
    expect(loyaltyApi.getPrintList).toHaveBeenCalledWith(5)
  })
})
