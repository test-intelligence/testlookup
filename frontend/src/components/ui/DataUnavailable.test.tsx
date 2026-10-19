/**
 * W29a UIX-11: the "we could not look" panel in the one empty / error look —
 * an alert with its reason, "HTTP {status}" and Retry, the warning icon tile,
 * and NO text painted in a status hue (no orange headline).
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import DataUnavailable from './DataUnavailable'

const http503 = { response: { status: 503 } }

describe('DataUnavailable', () => {
  it('is an alert with its title, message, status line and a Retry that calls onRetry', () => {
    const onRetry = vi.fn()
    render(<DataUnavailable error={http503} onRetry={onRetry} />)
    const panel = screen.getByRole('alert')
    expect(panel).toHaveAttribute('data-testid', 'data-unavailable')
    expect(within(panel).getByRole('heading', { name: 'The server failed to answer' })).toBeInTheDocument()
    expect(within(panel).getByText(/The backend returned 503/)).toBeInTheDocument()
    expect(within(panel).getByText('HTTP 503')).toBeInTheDocument()
    const retry = within(panel).getByRole('button', { name: /Retry/ })
    expect(retry).toHaveClass('btn-secondary', 'btn-sm')
    fireEvent.click(retry)
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('keeps a caller test id, and offers no Retry when retrying cannot help', () => {
    render(<DataUnavailable error={{ response: { status: 403 } }} onRetry={vi.fn()} testId="runs-unavailable" />)
    expect(screen.getByTestId('runs-unavailable')).toHaveAttribute('role', 'alert')
    expect(screen.getByText('HTTP 403')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Retry/ })).toBeNull()
  })

  it('an offline error has no status line', () => {
    render(<DataUnavailable error={new Error('Network Error')} onRetry={vi.fn()} />)
    expect(screen.getByRole('heading', { name: "Can't reach the server" })).toBeInTheDocument()
    expect(screen.queryByText(/^HTTP /)).toBeNull()
  })

  it('draws the warning only in its icon tile: no element colours its text with a status hue', () => {
    const { container } = render(<DataUnavailable error={http503} onRetry={vi.fn()} />)
    const panel = screen.getByRole('alert')
    expect(panel).toHaveClass('py-12')
    expect(container.innerHTML).not.toContain('--status-')
    const tile = panel.querySelector('svg')?.parentElement as HTMLElement
    expect(tile.style.background).toBe('var(--ui-warning-bg)')
    expect(tile.style.color).toBe('var(--ui-warning)')
    expect(screen.getByRole('heading')).toHaveClass('text-[var(--color-text-secondary)]')
  })
})
