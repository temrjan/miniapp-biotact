import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, it, expect, afterEach, vi } from 'vitest'
import { CopyTextButton } from './CopyTextButton'

describe('CopyTextButton', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('copies through the clipboard API when it exists', async () => {
    const user = userEvent.setup()
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('navigator', { ...window.navigator, clipboard: { writeText } })

    render(<CopyTextButton text="пост в канал" label="Скопировать текст отпечатка" />)
    await user.click(screen.getByRole('button', { name: 'Скопировать текст отпечатка' }))

    expect(writeText).toHaveBeenCalledWith('пост в канал')
    expect(await screen.findByRole('button', { name: 'Скопировано' })).toBeInTheDocument()
  })

  it('falls back to a pre-filled field when there is no clipboard — silently copying nothing is the worst outcome', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('navigator', { ...window.navigator, clipboard: undefined })

    render(<CopyTextButton text="пост в канал" label="Скопировать текст отмены" />)
    await user.click(screen.getByRole('button', { name: 'Скопировать текст отмены' }))

    expect(screen.getByDisplayValue('пост в канал')).toBeInTheDocument()
    expect(screen.getByText(/скопируйте вручную/)).toBeInTheDocument()
  })
})
