import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { Card } from './Card'

describe('Card', () => {
  it('renders children', () => {
    render(<Card>Card content</Card>)
    expect(screen.getByText('Card content')).toBeInTheDocument()
  })

  it('applies neumorphic class', () => {
    render(<Card data-testid="card">Content</Card>)
    const card = screen.getByTestId('card')
    expect(card).toHaveClass('neumorphic')
  })

  it('applies custom className', () => {
    render(<Card className="custom-class" data-testid="card">Content</Card>)
    expect(screen.getByTestId('card')).toHaveClass('custom-class')
  })

  it('handles click events', () => {
    const handleClick = vi.fn()
    render(
      <Card onClick={handleClick} data-testid="card">
        Clickable
      </Card>
    )
    fireEvent.click(screen.getByTestId('card'))
    expect(handleClick).toHaveBeenCalledTimes(1)
  })

  it('renders with default padding', () => {
    render(<Card data-testid="card">Content</Card>)
    expect(screen.getByTestId('card')).toHaveClass('p-2')
  })
})
