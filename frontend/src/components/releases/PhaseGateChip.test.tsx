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

  it('the tooltip is the first blocking reason, else "Live preview — nothing is recorded"', () => {
    const { rerender } = render(<PhaseGateChip result={result('NO_GO', ['checkout::abc is FAILED', 'checkout::def is BROKEN'])} />)
    expect(screen.getByTestId('phase-gate-chip')).toHaveAttribute('title', 'checkout::abc is FAILED')
    rerender(<PhaseGateChip result={result('GO')} />)
    expect(screen.getByTestId('phase-gate-chip')).toHaveAttribute('title', 'Live preview — nothing is recorded')
  })
})
