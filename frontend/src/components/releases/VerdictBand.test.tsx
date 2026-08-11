import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import VerdictBand from './VerdictBand'
import type { DerivedBlocker, DerivedRelease } from './types'

/**
 * Minimal ``DerivedRelease`` for the verdict-band render. Only the fields the
 * band reads (``name``, ``version``, ``gate.decision``, ``blockers``) carry
 * meaningful values; the rest are inert placeholders.
 */
function makeRelease(overrides: Partial<DerivedRelease> = {}): DerivedRelease {
  return {
    source: {} as DerivedRelease['source'],
    id: 'rel-1',
    name: 'Release',
    version: 'v1.0',
    ownerInitials: 'AB',
    ownerName: 'A B',
    gate: { decision: 'conditional', composite: 92, coverage: null, flakePct: null, updatedAt: null },
    totals: null,
    phases: [],
    blockers: [],
    dueAt: null,
    stage: 'in_progress',
    ...overrides,
  } as DerivedRelease
}

function blocker(severity: DerivedBlocker['severity'], title: string): DerivedBlocker {
  return {
    id: `b-${severity}`,
    severity,
    title,
    context: '',
    action: { label: 'View', href: '#' },
  }
}

describe('VerdictBand blocker severity icons', () => {
  it('maps each blocker severity to its per-theme status token, not raw palette classes', () => {
    const release = makeRelease({
      blockers: [
        blocker('resolved', 'Coverage restored'),
        blocker('warn', 'Flake rate elevated'),
        blocker('red', 'Composite under gate'),
      ],
    })

    const { container } = render(
      <VerdictBand highlighted={release} inProgressReleases={[release]} />,
    )

    // Titles render so the icons are actually mounted.
    expect(screen.getByText('Coverage restored')).toBeInTheDocument()
    expect(screen.getByText('Flake rate elevated')).toBeInTheDocument()
    expect(screen.getByText('Composite under gate')).toBeInTheDocument()

    const html = container.innerHTML
    // resolved → passed, warn → broken, red → failed.
    expect(html).toContain('text-[var(--status-passed)]')
    expect(html).toContain('text-[var(--status-broken)]')
    expect(html).toContain('text-[var(--status-failed)]')

    // Guard against regressing to the light-theme-illegible raw palette classes.
    expect(html).not.toContain('text-emerald-400')
    expect(html).not.toContain('text-amber-400')
    expect(html).not.toContain('text-red-400')
  })
})
