/**
 * W28 §2.7 (b): the automation split's two shares add to 100. Each half was
 * rounded on its own (13 + 21 read "Automated · 38% / Manual · 62%" only by
 * luck; 1 + 2 read 33% / 67%, but e.g. 3 + 5 read 38% / 63% = 101%); the
 * manual share is now the rest of 100.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { AutomationCoverageCard } from './InsightsPanel'

describe('AutomationCoverageCard split', () => {
  it.each([
    [13, 21, 38, 62],
    [1, 2, 33, 67],
    [0, 5, 0, 100],
    [5, 0, 100, 0],
    [3, 5, 38, 62],
    [1, 1, 50, 50],
  ])('%i automated + %i manual → automated %i, manual %i (percent)', (auto, manual, autoPct, manualPct) => {
    render(<AutomationCoverageCard auto={auto} manual={manual} />)
    expect(screen.getByText(`Automated · ${autoPct}%`)).toBeInTheDocument()
    expect(screen.getByText(`Manual · ${manualPct}%`)).toBeInTheDocument()
    expect(autoPct + manualPct).toBe(100)
    // The header sentence keeps the automated share as it was.
    expect(screen.getByText(`${autoPct}%`)).toBeInTheDocument()
  })

  it('says there is nothing to split with no cases', () => {
    render(<AutomationCoverageCard auto={0} manual={0} />)
    expect(screen.getByText('No authored cases yet — nothing to split.')).toBeInTheDocument()
    expect(screen.queryByText(/^Manual ·/)).toBeNull()
  })
})
