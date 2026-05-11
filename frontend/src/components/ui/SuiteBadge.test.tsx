import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import SuiteBadge from './SuiteBadge'

describe('SuiteBadge', () => {
  it('renders an em dash when there is no suite info', () => {
    render(<SuiteBadge primary={null} all={null} />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('renders the primary suite alone when there is exactly one', () => {
    render(<SuiteBadge primary="Smoke" all={['Smoke']} />)
    expect(screen.getByText('Smoke')).toBeInTheDocument()
    expect(screen.queryByText(/\+\d+/)).not.toBeInTheDocument()
  })

  it('shows "+N" when the run spans multiple suites', () => {
    render(<SuiteBadge primary="Smoke" all={['Smoke', 'Regression', 'Integration']} />)
    expect(screen.getByText('Smoke')).toBeInTheDocument()
    expect(screen.getByText('+2')).toBeInTheDocument()
  })

  it('uses the all-list tooltip with comma-joined suite names', () => {
    const { container } = render(
      <SuiteBadge primary="Smoke" all={['Smoke', 'Regression']} />,
    )
    // The outer chip is the first span in the tree; the inner span wrapping the
    // label text doesn't carry the tooltip.
    const chip = container.querySelector('span[title]')
    expect(chip).toHaveAttribute('title', 'Smoke, Regression')
  })

  it('falls back to the first item of `all` when `primary` is missing', () => {
    render(<SuiteBadge primary={null} all={['OnlySuite']} />)
    expect(screen.getByText('OnlySuite')).toBeInTheDocument()
  })
})
