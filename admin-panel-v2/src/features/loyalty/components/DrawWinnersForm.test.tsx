import { render, screen, within } from '@testing-library/react'
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

  it('does not send anything on the button click until the count of winners is typed', async () => {
    const user = userEvent.setup()
    renderWithClient(<DrawWinnersForm drawId={1} totalTickets={30} />)
    await user.type(screen.getByLabelText(/Выигравшие номера/), '17, 4 23')

    // The button is a request for confirmation, not the submission — recording winners is the one
    // irreversible operation in the system (cancel_draw answers 409 after it).
    await user.click(screen.getByRole('button', { name: 'Записать победителей' }))
    expect(loyaltyApi.recordWinners).not.toHaveBeenCalled()

    // The dialog names the count AND lists every parsed number: the count catches a pasted list
    // and a truncated one, the list catches a mistyped digit — the two channels cover each other
    // (Gate-1 ratification condition: the list must never collapse into a bare number).
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('Победителей: 3 — билеты №17, №4, №23.')

    const confirm = within(dialog).getByRole('button', { name: 'Записать победителей' })
    expect(confirm).toBeDisabled()

    await user.type(within(dialog).getByRole('textbox'), '2')
    expect(confirm).toBeDisabled() // a wrong count keeps the door shut
    expect(loyaltyApi.recordWinners).not.toHaveBeenCalled()

    await user.clear(within(dialog).getByRole('textbox'))
    await user.type(within(dialog).getByRole('textbox'), '3')
    expect(confirm).toBeEnabled()

    await user.click(confirm)
    expect(loyaltyApi.recordWinners).toHaveBeenCalledWith(1, [17, 4, 23])
  })

  it('cannot be bypassed from the keyboard', async () => {
    const user = userEvent.setup()
    renderWithClient(<DrawWinnersForm drawId={1} totalTickets={30} />)

    // Enter in the numbers field must not send anything past the confirmation.
    await user.type(screen.getByLabelText(/Выигравшие номера/), '17, 4 23{Enter}')
    expect(loyaltyApi.recordWinners).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Записать победителей' }))
    const dialog = screen.getByRole('dialog')

    // Enter in the confirmation field — empty, then with a wrong count — sends nothing either.
    await user.type(within(dialog).getByRole('textbox'), '{Enter}')
    expect(loyaltyApi.recordWinners).not.toHaveBeenCalled()
    await user.type(within(dialog).getByRole('textbox'), '2{Enter}')
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
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByRole('textbox'), '2')
    await user.click(within(dialog).getByRole('button', { name: 'Записать победителей' }))

    // Shown in the form, not behind a modal: the dialog closes on a refusal too, so the message
    // sits next to the field that has to be corrected.
    expect(await screen.findByText(/уже выиграл в этом розыгрыше/)).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
