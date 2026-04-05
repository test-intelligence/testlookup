import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import StatusBadge from './StatusBadge'

describe('StatusBadge', () => {
  it('maps known statuses to badge classes', () => {
    const { rerender } = render(<StatusBadge status="PASSED" />)
    expect(screen.getByText('PASSED')).toHaveClass('badge-passed')

    rerender(<StatusBadge status="FAILED" />)
    expect(screen.getByText('FAILED')).toHaveClass('badge-failed')
  })

  it('is case-insensitive for status lookup', () => {
    render(<StatusBadge status="in_progress" />)
    expect(screen.getByText('in_progress')).toHaveClass('bg-[var(--color-bg-secondary)]')
  })

  it('falls back to unknown styles for unsupported status', () => {
    render(<StatusBadge status="NOT_REAL" />)
    expect(screen.getByText('NOT_REAL')).toHaveClass('bg-[var(--color-bg-secondary)]')
  })

  it('merges custom className', () => {
    render(<StatusBadge status="PASSED" className="my-badge" />)
    expect(screen.getByText('PASSED')).toHaveClass('my-badge')
  })
})
