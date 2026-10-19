/**
 * W29a UIX-03: a gate decision as a chip — the one vocabulary's words, the
 * decision's tint / border and its legible ink; never `uppercase`.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import DecisionChip from './DecisionChip'

describe('DecisionChip', () => {
  it.each([
    ['GO', 'Go', 'go', 'passed'],
    ['CONDITIONAL_GO', 'Conditional', 'conditional', 'broken'],
    ['NO-GO', 'No-Go', 'no_go', 'failed'],
  ])('%s reads "%s" in the %s tokens', (raw, word, key, status) => {
    render(<DecisionChip decision={raw} title="Gate decision" />)
    const chip = screen.getByText(word)
    expect(chip).toHaveAttribute('data-decision', key)
    expect(chip).toHaveAttribute('title', 'Gate decision')
    expect(chip.style.color).toBe(`var(--status-${status}-ink)`)
    expect(chip.style.background).toBe(`var(--status-${status}-bg)`)
    expect(chip.style.borderColor).toBe(`var(--status-${status}-bd)`)
    expect(chip.className).not.toMatch(/uppercase|tracking/)
    expect(chip).toHaveClass('rounded-full', 'border', 'text-xs', 'font-semibold')
  })

  it('conditional never paints skipped-gold', () => {
    render(<DecisionChip decision="conditional" />)
    expect(screen.getByText('Conditional').getAttribute('style')).not.toContain('--status-skipped')
  })

  it('an unknown value prints its own words in the muted neutral, with no decision key', () => {
    render(<DecisionChip decision="PENDING_REVIEW" className="ml-2" />)
    const chip = screen.getByText('Pending review')
    expect(chip).toHaveAttribute('data-decision', '')
    expect(chip).toHaveClass('text-[var(--color-text-muted)]', 'ml-2')
    expect(chip.getAttribute('style')).toBeNull()
  })
})
