import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import StatusBadge from './StatusBadge'

// W29a UIX-03: the vocabulary's sentence-case words ("Failed", never the raw
// "FAILED"), the badge class by statusKey, and data-status for tests / styles.
describe('StatusBadge', () => {
  it('maps known statuses to badge classes and prints sentence case', () => {
    const { rerender } = render(<StatusBadge status="PASSED" />)
    expect(screen.getByText('Passed')).toHaveClass('badge-passed')
    expect(screen.getByText('Passed')).toHaveAttribute('data-status', 'passed')

    rerender(<StatusBadge status="FAILED" />)
    expect(screen.getByText('Failed')).toHaveClass('badge-failed')
    expect(screen.queryByText('FAILED')).not.toBeInTheDocument()

    for (const [raw, word, cls] of [
      ['BROKEN', 'Broken', 'badge-broken'],
      ['SKIPPED', 'Skipped', 'badge-skipped'],
      ['FLAKY', 'Flaky', 'badge-flaky'],
      ['PASSED_RUN', 'Passed', 'badge-passed'],
    ] as const) {
      rerender(<StatusBadge status={raw} />)
      expect(screen.getByText(word)).toHaveClass(cls)
    }
  })

  it('is case-insensitive: lower-case and run statuses read the same map', () => {
    const { rerender } = render(<StatusBadge status="failed" />)
    expect(screen.getByText('Failed')).toHaveClass('badge-failed')

    rerender(<StatusBadge status="in_progress" />)
    const running = screen.getByText('In progress')
    expect(running).toHaveClass('bg-[var(--color-bg-secondary)]')
    expect(running).toHaveClass('text-[var(--color-text-secondary)]')
    expect(running).toHaveAttribute('data-status', 'in_progress')

    rerender(<StatusBadge status="STOPPED" />)
    expect(screen.getByText('Stopped')).toHaveClass('text-[var(--color-text-muted)]')
  })

  it('an unknown status prints its own words in sentence case, in the muted neutral', () => {
    render(<StatusBadge status="NOT_REAL" />)
    const badge = screen.getByText('Not real')
    expect(badge).toHaveClass('bg-[var(--color-bg-secondary)]')
    expect(badge).toHaveClass('text-[var(--color-text-muted)]')
    expect(badge).toHaveAttribute('data-status', 'not_real')
  })

  it('merges custom className', () => {
    render(<StatusBadge status="PASSED" className="my-badge" />)
    expect(screen.getByText('Passed')).toHaveClass('my-badge')
  })
})
