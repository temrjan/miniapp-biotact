import { render, screen, waitFor } from '@testing-library/react'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter } from 'react-router-dom'
import type { ReactNode } from 'react'
import { HoldersPage } from './HoldersPage'
import { loyaltyApi } from '../api/loyaltyApi'
import type { TicketHolder, TicketHoldersResponse } from '../types'

vi.mock('../api/loyaltyApi', () => ({
  loyaltyApi: { getTicketHolders: vi.fn() },
}))

const holder = (over: Partial<TicketHolder> = {}): TicketHolder => ({
  user_id: 1,
  name: 'Иван Петров',
  phone: '+998901112233',
  tickets_total: 2,
  tickets_clean: 2,
  by_source: { survey: 1, friend: 0, activation: 1 },
  draw_number: null,
  numbers: [],
  played: 0,
  won: false,
  eligible_now: true,
  not_eligible_reason: null,
  last_activation_at: null,
  ...over,
})

const page = (items: TicketHolder[], total = items.length): TicketHoldersResponse => ({
  items,
  total,
  page: 1,
  size: 20,
})

const wrapper = ({ children }: { children: ReactNode }) => (
  <MemoryRouter initialEntries={['/loyalty/holders?filter=all']}>
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {children}
    </QueryClientProvider>
  </MemoryRouter>
)

const renderPage = () => render(<HoldersPage />, { wrapper })

describe('HoldersPage', () => {
  beforeEach(() => {
    vi.mocked(loyaltyApi.getTicketHolders).mockReset()
  })

  it('names what each member got their tickets for', async () => {
    vi.mocked(loyaltyApi.getTicketHolders).mockResolvedValue(page([holder()]))
    renderPage()

    expect(await screen.findByText('Иван Петров')).toBeInTheDocument()
    expect(screen.getByText('+998901112233')).toBeInTheDocument()
    expect(screen.getByText('анкета · покупка')).toBeInTheDocument()
  })

  it('counts repeated sources instead of listing them twice', async () => {
    vi.mocked(loyaltyApi.getTicketHolders).mockResolvedValue(
      page([holder({ by_source: { survey: 1, friend: 0, activation: 3 }, tickets_total: 4 })])
    )
    renderPage()

    expect(await screen.findByText('анкета · покупка ×3')).toBeInTheDocument()
  })

  it('reports one draw with the member own numbers, not the whole history', async () => {
    vi.mocked(loyaltyApi.getTicketHolders).mockResolvedValue(
      page([holder({ draw_number: 2, numbers: [4, 5], tickets_clean: 0 })])
    )
    renderPage()

    expect(await screen.findByText('Розыгрыш №2: 4, 5')).toBeInTheDocument()
  })

  it('names the reason a member will not enter the draw', async () => {
    vi.mocked(loyaltyApi.getTicketHolders).mockResolvedValue(
      page([holder({ eligible_now: false, not_eligible_reason: 'age' })])
    )
    renderPage()

    expect(await screen.findByText('нет — не подтверждено 18+')).toBeInTheDocument()
  })

  it('shows an empty state instead of an empty table', async () => {
    vi.mocked(loyaltyApi.getTicketHolders).mockResolvedValue(page([]))
    renderPage()

    expect(await screen.findByText(/по этому фильтру список пуст/)).toBeInTheDocument()
  })

  it('surfaces a failed load rather than pretending the list is empty', async () => {
    vi.mocked(loyaltyApi.getTicketHolders).mockRejectedValue(new Error('нет связи'))
    renderPage()

    await waitFor(() =>
      expect(screen.getByText(/Ошибка загрузки данных: нет связи/)).toBeInTheDocument()
    )
  })

  it('asks the API for the filter named in the URL', async () => {
    vi.mocked(loyaltyApi.getTicketHolders).mockResolvedValue(page([]))
    render(<HoldersPage />, {
      wrapper: ({ children }: { children: ReactNode }) => (
        <MemoryRouter initialEntries={['/loyalty/holders?filter=owed']}>
          <QueryClientProvider
            client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
          >
            {children}
          </QueryClientProvider>
        </MemoryRouter>
      ),
    })

    await waitFor(() =>
      expect(loyaltyApi.getTicketHolders).toHaveBeenCalledWith('owed', 1, 20)
    )
  })

  it('explains, on the owed slice, which rows mean a broken issuance', async () => {
    // The alarm moved out of the card's colour and into this ordering: a FRESH date at the top is
    // a real failure (activation issues every source), old dates and "never" are the expected
    // residue of not backfilling. Without this sentence the column is just a date.
    vi.mocked(loyaltyApi.getTicketHolders).mockResolvedValue(
      page([holder({ last_activation_at: '2026-08-03T10:00:00Z', tickets_total: 0 })])
    )
    render(<HoldersPage />, {
      wrapper: ({ children }: { children: ReactNode }) => (
        <MemoryRouter initialEntries={['/loyalty/holders?filter=owed']}>
          <QueryClientProvider
            client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
          >
            {children}
          </QueryClientProvider>
        </MemoryRouter>
      ),
    })

    expect(await screen.findByText(/Свежая дата в верхних строках/)).toBeInTheDocument()
    // Awaited separately: the hint renders before the request settles, the row only after it.
    expect(await screen.findByText('03.08.2026')).toBeInTheDocument()
  })

  it('says "ни разу" for a member who never activated a QR', async () => {
    vi.mocked(loyaltyApi.getTicketHolders).mockResolvedValue(page([holder()]))
    render(<HoldersPage />, {
      wrapper: ({ children }: { children: ReactNode }) => (
        <MemoryRouter initialEntries={['/loyalty/holders?filter=owed']}>
          <QueryClientProvider
            client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
          >
            {children}
          </QueryClientProvider>
        </MemoryRouter>
      ),
    })

    expect(await screen.findByText('ни разу')).toBeInTheDocument()
  })
})
