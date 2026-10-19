/**
 * REL-01 (D-2): a phase row's live gate chip. Its tooltip is the first
 * blocking reason, or says that a live preview records nothing.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { PhaseGateResult } from '@/types/releases'
import PhaseGateChip from './PhaseGateChip'

const result = (verdict: string, blocking: string[] = []): PhaseGateResult => ({
  phase_id: 'ph-1', phase_name: 'Regression', verdict, blocking_reasons: blocking,
  may_exit: verdict === 'GO', recorded: false, scorecard: null,
})

describe('PhaseGateChip', () => {
  it.each([
    ['GO', 'Gate: Go'],
    ['NO_GO', 'Gate: No-Go'],
    ['NOT_EVALUATED', 'Gate: Not evaluated'],
    ['SOMETHING_NEW', 'Gate: Not evaluated'],
  ])('%s reads "%s"', (verdict, text) => {
    render(<PhaseGateChip result={result(verdict)} />)
    const chip = screen.getByTestId('phase-gate-chip')
    expect(chip).toHaveTextContent(text)
    expect(chip).toHaveAttribute('data-verdict', verdict)
  })

  // W29a UIX-03: the one decision vocabulary's word; conditional is the warning
  // hue (broken), never skipped-gold; every decided chip's text is its ink.
  it('CONDITIONAL_GO reads "Gate: Conditional" in the warning hue, never skipped-gold', () => {
    render(<PhaseGateChip result={result('CONDITIONAL_GO')} />)
    const chip = screen.getByTestId('phase-gate-chip')
    expect(chip).toHaveTextContent('Gate: Conditional')
    expect(chip.getAttribute('style') ?? '').not.toMatch(/--status-skipped/)
    expect(chip.style.color).toBe('var(--status-broken-ink)')
    expect(chip.style.borderColor).toBe('var(--status-broken-bd)')
  })

  it.each([
    ['GO', 'var(--status-passed-ink)'],
    ['NO_GO', 'var(--status-failed-ink)'],
    ['NOT_EVALUATED', 'var(--color-text-muted)'],
  ])('%s: the text is %s', (verdict, colour) => {
    render(<PhaseGateChip result={result(verdict)} />)
    expect(screen.getByTestId('phase-gate-chip').style.color).toBe(colour)
  })

  it('the tooltip is the first blocking reason, else "Live preview — nothing is recorded"', () => {
    const { rerender } = render(<PhaseGateChip result={result('NO_GO', ['checkout::abc is FAILED', 'checkout::def is BROKEN'])} />)
    expect(screen.getByTestId('phase-gate-chip')).toHaveAttribute('title', 'checkout::abc is FAILED')
    rerender(<PhaseGateChip result={result('GO')} />)
    expect(screen.getByTestId('phase-gate-chip')).toHaveAttribute('title', 'Live preview — nothing is recorded')
  })
})
