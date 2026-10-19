import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import LoadingSpinner from './LoadingSpinner'

describe('LoadingSpinner', () => {
  it('uses medium size by default', () => {
    const { container } = render(<LoadingSpinner />)
    expect(container.firstChild).toHaveClass('h-8', 'w-8')
  })

  it('applies small and large size classes', () => {
    const { rerender, container } = render(<LoadingSpinner size="sm" />)
    expect(container.firstChild).toHaveClass('h-4', 'w-4')

    rerender(<LoadingSpinner size="lg" />)
    expect(container.firstChild).toHaveClass('h-12', 'w-12')
  })

  it('accepts additional class names', () => {
    const { container } = render(<LoadingSpinner className="custom-spinner" />)
    expect(container.firstChild).toHaveClass('custom-spinner')
  })

  // W29a UIX-01 L1: the moving arc is the accent; a white arc vanished on the light themes.
  it('draws its arc in the accent, and nothing in white', () => {
    const { container } = render(<LoadingSpinner />)
    const spinner = container.firstChild as HTMLElement
    expect(spinner).toHaveClass('border-t-[var(--color-accent)]')
    expect(spinner.className).not.toMatch(/white/)
  })
})
