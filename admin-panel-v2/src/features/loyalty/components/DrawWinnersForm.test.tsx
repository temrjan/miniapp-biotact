import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { DrawWinnersForm } from './DrawWinnersForm'
import { loyaltyApi } from '../api/loyaltyApi'

vi.mock('../api/loyaltyApi', () => ({
  loyaltyApi: { recordWinners: vi.fn() },
}))

function renderWithClient(ui: ReactNode) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
}

describe('DrawWinnersForm', () => {
  beforeEach(() => {
    vi.mocked(loyaltyApi.recordWinners).mockReset()
  })

  it('shows how the free-form input was parsed, in prize order, before submitting', async () => {
    const user = userEvent.setup()
    renderWithClient(<DrawWinnersForm drawId={1} totalTickets={30} />)

    await user.type(screen.getByLabelText(/Выигравшие номера/), '17, 4 23')

    expect(screen.getByText('Главный приз: билет №17')).toBeInTheDocument()
    expect(screen.getByText('2-й приз: билет №4')).toBeInTheDocument()
    expect(screen.getByText('3-й приз: билет №23')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Записать победителей' }))
    expect(loyaltyApi.recordWinners).toHaveBeenCalledWith(1, [17, 4, 23])
  })

  it('flags garbage and out-of-range numbers before the server ever sees them', async () => {
    const user = userEvent.setup()
    renderWithClient(<DrawWinnersForm drawId={1} totalTickets={30} />)

    await user.type(screen.getByLabelText(/Выигравшие номера/), '5, abc, 99')

    expect(screen.getByText('Не номера: abc')).toBeInTheDocument()
    expect(screen.getByText(/Вне диапазона 1..30: 99/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Записать победителей' })).toBeDisabled()
    expect(loyaltyApi.recordWinners).not.toHaveBeenCalled()
  })

  it('shows the all-or-nothing server refusal as text', async () => {
    const user = userEvent.setup()
    vi.mocked(loyaltyApi.recordWinners).mockRejectedValue({
      isAxiosError: true,
      message: 'Request failed with status code 409',
      response: {
        data: { detail: 'Номер 4 принадлежит участнику, который уже выиграл в этом розыгрыше.' },
      },
    })
    renderWithClient(<DrawWinnersForm drawId={1} totalTickets={30} />)

    await user.type(screen.getByLabelText(/Выигравшие номера/), '17, 4')
    await user.click(screen.getByRole('button', { name: 'Записать победителей' }))

    expect(await screen.findByText(/уже выиграл в этом розыгрыше/)).toBeInTheDocument()
  })
})
