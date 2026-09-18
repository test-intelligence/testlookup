/**
 * The release KPI strip must not state a number it did not count.
 *
 * Regression for TL-2026-09-18-01-003. Every card used to carry a hardcoded
 * `sparkline` — `[4, 5, 4, 6, 7, 6, 7, inProgress.length || 6]` — so the strip
 * drew a seven-point trend nobody measured, told screen readers `trend ending
 * at N`, and rendered a real 0 as a 6 through the `|| 6` fallback. Two captions
 * were invented outright: `avg gate 97.1%` and `100% audit-packed`.
 *
 * `ReleasesPage` renders `<KpiStrip releases={derived} />` with no `override`,
 * so every one of those shipped on every view of the page.
 *
 * `OverviewPage` was fixed for the same rule in 2026-08 ("a KPI caption must
 * not deny its own value"): no trend line without history, and a caption that
 * explains the missing trend rather than asserting a metric. These tests hold
 * KpiStrip to it.
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import KpiStrip from './KpiStrip'

type Release = Parameters<typeof KpiStrip>[0]['releases'][number]

function release(overrides: Partial<Release> = {}): Release {
  return {
    stage: 'in_progress',
    gate: { decision: 'no_go' },
    blockers: [],
    source: {},
    ...overrides,
  } as Release
}

describe('KpiStrip states only what it counted', () => {
  it('draws no trend line — a release list carries no history', () => {
    const { container } = render(
      <KpiStrip releases={[release(), release(), release()]} />,
    )
    expect(
      container.querySelectorAll('svg[aria-label^="trend"]'),
      'a sparkline here can only be invented: nothing supplies a time series',
    ).toHaveLength(0)
  })

  it('each displayed value equals the count it claims', () => {
    // Two in progress (one at gate `go`), one blocked, one already released.
    //
    // The released one ALSO sits at gate `go`. That is deliberate: "ready to
    // ship" counts in-progress releases at `go`, so a fixture whose only `go`
    // release is in progress cannot tell `inProgress.filter(go)` from
    // `releases.filter(go)`. The first version of this test could not, and a
    // mutation swapping one for the other passed it.
    render(
      <KpiStrip
        releases={[
          release({ gate: { decision: 'go' } } as Partial<Release>),
          release({ blockers: [{ severity: 'red' }] } as Partial<Release>),
          release({
            stage: 'released',
            gate: { decision: 'go' },
            source: { released_at: new Date().toISOString() },
          } as Partial<Release>),
        ]}
      />,
    )
    const valueOf = (label: string) =>
      screen.getByText(label).closest('div')?.parentElement?.querySelector('.tabular-nums')?.textContent
    expect(valueOf('In progress')).toBe('2')
    expect(valueOf('Ready to ship')).toBe('1')
    expect(valueOf('Blocked')).toBe('1')
    expect(valueOf('Released · 30d')).toBe('1')
  })

  it('shows four genuine zeros for an empty release list', () => {
    render(<KpiStrip releases={[]} />)
    const values = [...document.querySelectorAll('.tabular-nums')].map((n) => n.textContent)
    expect(values).toEqual(['0', '0', '0', '0'])
  })

  it('states no gate percentage or audit-pack rate it did not compute', () => {
    render(<KpiStrip releases={[release(), release({ gate: { decision: 'go' } } as Partial<Release>)]} />)
    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/97\.1/)
    expect(text).not.toMatch(/audit-packed/i)
    // "from last week" described a week-over-week comparison the card does not make.
    expect(text).not.toMatch(/from last week/i)
  })

  it('still reports the counts it does compute', () => {
    render(
      <KpiStrip
        releases={[
          release({ gate: { decision: 'go' } } as Partial<Release>),
          release(),
          release({ blockers: [{ severity: 'red' }] } as Partial<Release>),
        ]}
      />,
    )
    expect(screen.getByText('In progress')).toBeInTheDocument()
    expect(screen.getByText('Ready to ship')).toBeInTheDocument()
    expect(screen.getByText('Blocked')).toBeInTheDocument()
    expect(screen.getByText('Released · 30d')).toBeInTheDocument()
  })

  it('surfaces the Planning count when nothing has been promoted yet', () => {
    render(<KpiStrip releases={[release({ stage: 'planning' }), release({ stage: 'planning' })]} />)
    expect(document.body.textContent).toMatch(/2.*at Planning/)
  })

  it('honours an override, which is the only supported source of a real trend', () => {
    const { container } = render(
      <KpiStrip
        releases={[]}
        override={[{ label: 'Measured', value: 7, sparkline: [1, 2, 3, 4] }]}
      />,
    )
    expect(screen.getByText('Measured')).toBeInTheDocument()
    expect(container.querySelectorAll('svg[aria-label^="trend"]')).toHaveLength(1)
  })
})
