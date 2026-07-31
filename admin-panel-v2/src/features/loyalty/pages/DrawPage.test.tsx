import { render, screen } from '@testing-library/react'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import type { ReactNode } from 'react'
import { DrawPage } from './DrawPage'
import { loyaltyApi } from '../api/loyaltyApi'
import type { CurrentDraw, CurrentDrawResponse } from '../types'

vi.mock('../api/loyaltyApi', () => ({
  loyaltyApi: { getCurrentDraw: vi.fn() },
}))

const committedDraw: CurrentDraw = {
  draw_id: 5,
  draw_number: 3,
  status: 'committed',
  total_tickets: 42,
  participant_hash: 'ab'.repeat(32),
  conducted_on: '2026-09-01',
  prize_label: null,
  committed_at: '2026-07-31T10:00:00Z',
  drawn_at: null,
  cancelled_at: null,
  winners: [],
}

const response = (
  draw: CurrentDraw | null,
  lastDrawn: CurrentDraw | null = null
): CurrentDrawResponse => ({
  eligible_tickets: 7,
  clean_tickets: 9,
  draw,
  last_drawn: lastDrawn,
})

function renderWithClient(ui: ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>
  )
}

describe('DrawPage — four states by draw status', () => {
  beforeEach(() => {
    vi.mocked(loyaltyApi.getCurrentDraw).mockReset()
  })

  it('no draw: the freeze card with post-filter and total numbers', async () => {
    vi.mocked(loyaltyApi.getCurrentDraw).mockResolvedValue(response(null))
    renderWithClient(<DrawPage />)

    expect(await screen.findByText('К заморозке готово 7 билетов')).toBeInTheDocument()
    expect(screen.getByText(/Всего чистых билетов: 9/)).toBeInTheDocument()
  })

  it('committed: fingerprint, print link, winners form, cancel button', async () => {
    vi.mocked(loyaltyApi.getCurrentDraw).mockResolvedValue(response(committedDraw))
    renderWithClient(<DrawPage />)

    expect(await screen.findByText('Розыгрыш №3 заморожен')).toBeInTheDocument()
    expect(screen.getByText('ab'.repeat(32))).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Печатный список/ })).toHaveAttribute(
      'href',
      '/loyalty/draw/print/5'
    )
    expect(screen.getByRole('button', { name: 'Отменить тираж' })).toBeInTheDocument()
    expect(screen.getByText('Победители')).toBeInTheDocument()
  })

  it('drawn: winners table and the next-freeze card', async () => {
    vi.mocked(loyaltyApi.getCurrentDraw).mockResolvedValue(
      response({
        ...committedDraw,
        status: 'drawn',
        winners: [
          { ticket_number: 17, prize_rank: 1, user_id: 11, first_name: 'Азиза' },
          { ticket_number: 4, prize_rank: 2, user_id: 12, first_name: null },
        ],
      })
    )
    renderWithClient(<DrawPage />)

    expect(await screen.findByText('Розыгрыш №3 разыгран')).toBeInTheDocument()
    expect(screen.getByText('№17')).toBeInTheDocument()
    expect(screen.getByText('Азиза')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Заморозить следующий' })).toBeInTheDocument()
  })

  it('committed with a previous drawn draw: its winners stay visible (Гейт-2, БЛОКЕР-1)', async () => {
    vi.mocked(loyaltyApi.getCurrentDraw).mockResolvedValue(
      response(committedDraw, {
        ...committedDraw,
        draw_id: 4,
        draw_number: 2,
        status: 'drawn',
        winners: [{ ticket_number: 7, prize_rank: 1, user_id: 21, first_name: 'Дилноза' }],
      })
    )
    renderWithClient(<DrawPage />)

    // the live draw is the page…
    expect(await screen.findByText('Розыгрыш №3 заморожен')).toBeInTheDocument()
    // …and the previous result is still reachable
    expect(screen.getByText('Розыгрыш №2 разыгран')).toBeInTheDocument()
    expect(screen.getByText('Дилноза')).toBeInTheDocument()
  })

  it('cancelled: the cancellation text is shown, not hidden (Р-2)', async () => {
    vi.mocked(loyaltyApi.getCurrentDraw).mockResolvedValue(
      response({ ...committedDraw, status: 'cancelled', cancelled_at: '2026-07-31T12:00:00Z' })
    )
    renderWithClient(<DrawPage />)

    expect(await screen.findByText('Розыгрыш №3 отменён')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Скопировать текст отмены' })
    ).toBeInTheDocument()
  })

  it('read error: dashboard-pattern message, not a silent spinner', async () => {
    vi.mocked(loyaltyApi.getCurrentDraw).mockRejectedValue(new Error('boom'))
    renderWithClient(<DrawPage />)

    expect(await screen.findByText(/Ошибка загрузки данных/)).toBeInTheDocument()
  })
})
