import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ShippingThisWeek } from './RightRail'
import type { DerivedRelease, ReleaseStage } from './types'

/**
 * Build a minimal ``DerivedRelease`` for the shipping-panel filter test. Only
 * the fields ``ShippingThisWeek`` reads (``name``, ``version``, ``stage``,
 * ``dueAt``, ``gate.decision``) carry meaningful values; the rest are inert
 * placeholders.
 */
function makeRelease(overrides: Partial<DerivedRelease> & { dueAt: string | null; stage: ReleaseStage }): DerivedRelease {
  return {
    source: {} as DerivedRelease['source'],
    id: 'rel-1',
    name: 'Release',
    version: 'v1.0',
    ownerInitials: 'AB',
    ownerName: 'A B',
    gate: { decision: 'go', composite: null, coverage: null, flakePct: null, updatedAt: null },
    totals: null,
    phases: [],
    blockers: [],
    ...overrides,
  }
}

describe('ShippingThisWeek', () => {
  it('keeps in-progress releases with a due date inside the 7-day window', () => {
    const today = new Date()
    render(
      <ShippingThisWeek
        releases={[
          makeRelease({ id: 'soon', name: 'Soon', stage: 'in_progress', dueAt: today.toISOString() }),
        ]}
        onOpen={vi.fn()}
      />,
    )

    expect(screen.getByText('Soon')).toBeInTheDocument()
    expect(screen.queryByText(/No releases due/i)).not.toBeInTheDocument()
  })

  it('drops releases whose dueAt is null (the narrowed-filter guard)', () => {
    render(
      <ShippingThisWeek
        releases={[
          makeRelease({ id: 'nodate', name: 'NoDate', stage: 'in_progress', dueAt: null }),
        ]}
        onOpen={vi.fn()}
      />,
    )

    expect(screen.queryByText('NoDate')).not.toBeInTheDocument()
    expect(screen.getByText(/No releases due/i)).toBeInTheDocument()
  })

  it('drops releases that are not in progress even when a due date is set', () => {
    const today = new Date()
    render(
      <ShippingThisWeek
        releases={[
          makeRelease({ id: 'done', name: 'Done', stage: 'released', dueAt: today.toISOString() }),
        ]}
        onOpen={vi.fn()}
      />,
    )

    expect(screen.queryByText('Done')).not.toBeInTheDocument()
    expect(screen.getByText(/No releases due/i)).toBeInTheDocument()
  })
})
