import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { DrawFreezeCard } from './DrawFreezeCard'
import { loyaltyApi } from '../api/loyaltyApi'

vi.mock('../api/loyaltyApi', () => ({
  loyaltyApi: { commitDraw: vi.fn() },
}))

function renderWithClient(ui: ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
}

describe('DrawFreezeCard', () => {
  beforeEach(() => {
    vi.mocked(loyaltyApi.commitDraw).mockReset()
  })

  it('keeps confirm disabled until the administrator types the POST-FILTER number', async () => {
    const user = userEvent.setup()
    renderWithClient(<DrawFreezeCard eligibleTickets={7} cleanTickets={9} />)

    // both numbers visible, with the difference explained (Р-3)
    expect(screen.getByText('К заморозке готово 7 билетов')).toBeInTheDocument()
    expect(screen.getByText(/Всего чистых билетов: 9/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Заморозить' }))
    const confirm = screen.getAllByRole('button', { name: 'Заморозить' }).at(-1)
    expect(confirm).toBeDisabled()

    await user.type(screen.getByRole('textbox'), '9')
    expect(confirm).toBeDisabled() // the dashboard number, not the eligible one

    await user.clear(screen.getByRole('textbox'))
    await user.type(screen.getByRole('textbox'), '7')
    expect(confirm).toBeEnabled()
  })

  it('sends the typed (eligible) number to the server', async () => {
    const user = userEvent.setup()
    vi.mocked(loyaltyApi.commitDraw).mockResolvedValue({
      draw_id: 1,
      draw_number: 1,
      total_tickets: 7,
      participant_hash: 'ab',
      committed_at: '2026-07-31T10:00:00Z',
      conducted_on: null,
    })
    renderWithClient(<DrawFreezeCard eligibleTickets={7} cleanTickets={7} />)

    await user.click(screen.getByRole('button', { name: 'Заморозить' }))
    await user.type(screen.getByRole('textbox'), '7')
    await user.click(screen.getAllByRole('button', { name: 'Заморозить' }).at(-1)!)

    expect(loyaltyApi.commitDraw).toHaveBeenCalledWith({ expected_tickets: 7 })
  })

  it('disables the button outright when nothing is eligible (Гейт-1, уточнение 3)', () => {
    renderWithClient(<DrawFreezeCard eligibleTickets={0} cleanTickets={0} />)

    expect(screen.getByRole('button', { name: 'Заморозить' })).toBeDisabled()
    expect(screen.getByText('Нет билетов к заморозке.')).toBeInTheDocument()
  })

  it('shows the server refusal as the server wrote it', async () => {
    const user = userEvent.setup()
    vi.mocked(loyaltyApi.commitDraw).mockRejectedValue({
      isAxiosError: true,
      message: 'Request failed with status code 409',
      response: {
        data: { detail: 'Подтверждение не совпало: вы указали 7, а к заморозке готово 8 билетов.' },
      },
    })
    renderWithClient(<DrawFreezeCard eligibleTickets={7} cleanTickets={7} />)

    await user.click(screen.getByRole('button', { name: 'Заморозить' }))
    await user.type(screen.getByRole('textbox'), '7')
    await user.click(screen.getAllByRole('button', { name: 'Заморозить' }).at(-1)!)

    expect(
      await screen.findByText(/вы указали 7, а к заморозке готово 8 билетов/)
    ).toBeInTheDocument()
  })
})
