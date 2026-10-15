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
type Gate = Release['gate']

/** A recorded verdict in the §1.4 shape; only `decision` matters to the counts. */
function gate(decision: Gate['decision']): Gate {
  return {
    decision,
    passRate: null,
    denominator: null,
    evidence: null,
    runCount: null,
    decidedAt: null,
    measured: false,
    insufficientReason: null,
    blockingTotal: 0,
  }
}

const RED_BLOCKER: Release['blockers'][number] = {
  id: 'b-red',
  severity: 'red',
  title: 'checkout::test_pay is FAILED',
  context: 'release gate',
  action: { label: 'Open gate →', href: '/releases/r#gate' },
}

/** A full `DerivedRelease` (in progress, No-Go, no blockers) with `overrides` applied. */
function release(overrides: Partial<Release> = {}): Release {
  const blockers = overrides.blockers ?? []
  return {
    source: {} as Release['source'],
    id: 'rel',
    name: 'Release',
    version: '',
    stage: 'in_progress',
    ownerInitials: '–',
    ownerName: 'No owner',
    hasOwner: false,
    isActive: false,
    externalUrl: null,
    sourceSystem: null,
    dueAt: null,
    gate: gate('no_go'),
    totals: null,
    phases: [],
    blockers,
    blockerCount: blockers.length,
    ...overrides,
  }
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
          release({ gate: gate('go') }),
          release({ blockers: [RED_BLOCKER] }),
          release({
            stage: 'released',
            gate: gate('go'),
            source: { released_at: new Date().toISOString() } as Release['source'],
          }),
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
    render(<KpiStrip releases={[release(), release({ gate: gate('go') })]} />)
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
          release({ gate: gate('go') }),
          release(),
          release({ blockers: [RED_BLOCKER] }),
        ]}
      />,
    )
    expect(screen.getByText('In progress')).toBeInTheDocument()
    expect(screen.getByText('Ready to ship')).toBeInTheDocument()
    expect(screen.getByText('Blocked')).toBeInTheDocument()
    expect(screen.getByText('Released · 30d')).toBeInTheDocument()
  })

  it('counts every blocker the server recorded on an in-progress release under Blocked', () => {
    // 25 recorded blocking reasons, 5 kept on the derived release: the strip
    // states the server's 25 (`blockerCount`), not the 5 rows.
    render(
      <KpiStrip
        releases={[
          release({ blockers: Array.from({ length: 5 }, (_, i) => ({ ...RED_BLOCKER, id: `b${i}` })), blockerCount: 25 }),
          // A released release's blockers are not open ones.
          release({ stage: 'released', blockers: [RED_BLOCKER], blockerCount: 7 }),
        ]}
      />,
    )
    expect(screen.getByText('25', { selector: 'strong' }).parentElement).toHaveTextContent('25 blocking items open')
  })

  it('says "no blockers" under Blocked when nothing in progress is blocked', () => {
    render(<KpiStrip releases={[release({ gate: gate('go') })]} />)
    expect(screen.getByText('no blockers')).toBeInTheDocument()
  })

  it('surfaces the Planning count when nothing has been promoted yet', () => {
    render(<KpiStrip releases={[release({ stage: 'planning' }), release({ stage: 'planning' })]} />)
    expect(document.body.textContent).toMatch(/2.*at Planning/)
  })

  it('honours an override, and still draws no trend line (the prop that could is gone)', () => {
    const { container } = render(
      <KpiStrip releases={[]} override={[{ label: 'Measured', value: 7 }]} />,
    )
    expect(screen.getByText('Measured')).toBeInTheDocument()
    expect(screen.getByText('7')).toBeInTheDocument()
    expect(container.querySelectorAll('svg[aria-label^="trend"]')).toHaveLength(0)
  })
})

// OD-11 (Wave 2.5): the sub-line was an HTML string rendered with
// `dangerouslySetInnerHTML`. It is JSX now, so markup in it is text.
describe('KpiStrip renders its sub-line as text, never as HTML', () => {
  it('keeps the emphasised count as an element, built in JSX', () => {
    render(<KpiStrip releases={[release({ stage: 'planning' }), release({ stage: 'planning' })]} />)
    const strong = screen.getByText('2', { selector: 'strong' })
    expect(strong.parentElement).toHaveTextContent('2 at Planning · promote one to start tracking')
  })

  it('shows markup handed to it as literal text — no element is created from it', () => {
    const hostile = '<img src=x onerror="alert(1)"><strong>bold</strong>'
    const { container } = render(
      <KpiStrip releases={[]} override={[{ label: 'Hostile', value: 1, sub: hostile }]} />,
    )
    expect(screen.getByText(hostile)).toBeInTheDocument()
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('strong')).toBeNull()
  })
})
