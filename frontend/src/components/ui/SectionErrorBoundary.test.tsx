import { afterEach, describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SectionErrorBoundary } from './SectionErrorBoundary'

// Suppress console.error from React's error boundary logging
const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

function ThrowingChild({ shouldThrow }: { shouldThrow: boolean }) {
  if (shouldThrow) throw new Error('Test explosion')
  return <div data-testid="child">OK</div>
}

describe('SectionErrorBoundary', () => {
  afterEach(() => {
    consoleSpy.mockClear()
  })

  it('renders children when no error', () => {
    render(
      <SectionErrorBoundary>
        <div data-testid="child">Hello</div>
      </SectionErrorBoundary>,
    )
    expect(screen.getByTestId('child')).toHaveTextContent('Hello')
  })

  it('renders fallback message when child throws', () => {
    render(
      <SectionErrorBoundary message="Chart failed">
        <ThrowingChild shouldThrow />
      </SectionErrorBoundary>,
    )
    expect(screen.getByText('Chart failed')).toBeInTheDocument()
    expect(screen.getByText('Test explosion')).toBeInTheDocument()
  })

  it('renders default message when no custom message provided', () => {
    render(
      <SectionErrorBoundary>
        <ThrowingChild shouldThrow />
      </SectionErrorBoundary>,
    )
    expect(screen.getByText('This section failed to load')).toBeInTheDocument()
  })

  it('shows retry button that resets the boundary', () => {
    const { rerender } = render(
      <SectionErrorBoundary message="Failed">
        <ThrowingChild shouldThrow />
      </SectionErrorBoundary>,
    )
    expect(screen.getByText('Failed')).toBeInTheDocument()

    rerender(
      <SectionErrorBoundary message="Failed">
        <ThrowingChild shouldThrow={false} />
      </SectionErrorBoundary>,
    )

    fireEvent.click(screen.getByText('Try again'))

    // Boundary reset should re-render the child without an error.
    expect(screen.getByTestId('child')).toHaveTextContent('OK')
  })

  it('does not affect sibling boundaries', () => {
    render(
      <div>
        <SectionErrorBoundary message="Section A">
          <ThrowingChild shouldThrow />
        </SectionErrorBoundary>
        <SectionErrorBoundary message="Section B">
          <div data-testid="section-b">Still works</div>
        </SectionErrorBoundary>
      </div>,
    )
    expect(screen.getByText('Section A')).toBeInTheDocument()
    expect(screen.getByTestId('section-b')).toHaveTextContent('Still works')
  })
})
