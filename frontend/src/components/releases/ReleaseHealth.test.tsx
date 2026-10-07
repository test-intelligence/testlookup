/**
 * UX redesign P6: /releases has ONE StatusBanner above the list (the template
 * allows a banner OR a KPI strip, never both), and every fact the former
 * verdict band and KPI strip showed is still on screen — in the banner, or in
 * the collapsed "Release health · this week" disclosure under the list.
 *
 * The verdict band's own tests (blocker severity tokens, the not-evaluated
 * headline) moved here with its sentence and its blocker list.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ReleaseHealthBanner, ReleaseHealthDetails } from './ReleaseHealth'
import { countReleases, releaseHealthFacts, releaseHeadline } from './health'
import type { DerivedBlocker, DerivedRelease } from './types'

/** A `DerivedRelease` with only what the health views read set to something meaningful. */
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

function blocker(severity: DerivedBlocker['severity'], title: string, context = ''): DerivedBlocker {
  return { id: `b-${severity}-${title}`, severity, title, context, action: { label: 'View', href: '#' } }
}

const NOT_EVALUATED = { decision: 'not_evaluated', composite: null, coverage: null, flakePct: null, updatedAt: null } as const

/** In progress (not evaluated, a red blocker), in progress at go, planning, released 2 days ago. */
function project(): { next: DerivedRelease; all: DerivedRelease[] } {
  const next = makeRelease({
    id: 'rel-next',
    name: '2026.11',
    version: '2026.11',
    gate: { ...NOT_EVALUATED },
    blockers: [blocker('red', 'Regression failed', 'Regression')],
  })
  const ready = makeRelease({
    id: 'rel-ready',
    name: '2026.10.1',
    version: '2026.10.1',
    gate: { decision: 'go', composite: 97, coverage: null, flakePct: null, updatedAt: null },
  })
  const planned = makeRelease({ id: 'rel-plan', name: '2026.12', stage: 'planning', gate: { ...NOT_EVALUATED } })
  const shipped = makeRelease({
    id: 'rel-shipped',
    name: '2026.10',
    stage: 'released',
    gate: { ...NOT_EVALUATED },
    source: { released_at: new Date(Date.now() - 2 * 24 * 3600 * 1000).toISOString() } as DerivedRelease['source'],
  })
  return { next, all: [next, ready, planned, shipped] }
}

const facts = (container: HTMLElement) =>
  Array.from(container.querySelectorAll('[data-banner-fact]'), (fact) => fact.textContent?.trim())

describe('ReleaseHealthBanner — one line above the list', () => {
  it('is ONE StatusBanner: the gate pill, the sentence about the release that ships next, four counted facts', () => {
    const { next, all } = project()
    const inProgress = all.filter((r) => r.stage === 'in_progress')
    const { container } = render(<ReleaseHealthBanner highlighted={next} inProgressReleases={inProgress} releases={all} />)
    expect(container.querySelectorAll('[data-status-banner]')).toHaveLength(1)
    expect(container.querySelector('[data-status-banner]')).toHaveAttribute('data-status-banner', 'pending')
    expect(container.querySelector('[data-banner-pill]')).toHaveTextContent('NOT EVALUATED')
    // The name once: a version equal to the name is not repeated.
    expect(screen.getByText('2026.11 is in progress, not evaluated yet')).toBeInTheDocument()
    // Counted from the list: one blocker open, one ready, one blocked, one shipped in 30 days.
    expect(facts(container)).toEqual(['Open blockers 1', 'Ready to ship 1', 'Blocked 1', 'Released · 30d 1'])
    // No KPI cards above the list.
    expect(screen.queryByText('In progress')).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Release health' })).toBeInTheDocument()
  })

  it('maps each gate decision to its banner state, keeping the gate badge words where the state would misname it', () => {
    const cases = [
      ['go', 'go', 'GO'],
      ['conditional', 'conditional', 'CONDITIONAL GO'],
      ['no_go', 'no_go', 'NO-GO'],
      ['not_evaluated', 'pending', 'NOT EVALUATED'],
    ] as const
    for (const [decision, state, pill] of cases) {
      const release = makeRelease({ gate: { decision, composite: decision === 'not_evaluated' ? null : 91, coverage: null, flakePct: null, updatedAt: null } })
      const { container, unmount } = render(<ReleaseHealthBanner highlighted={release} inProgressReleases={[release]} releases={[release]} />)
      expect(container.querySelector('[data-status-banner]'), decision).toHaveAttribute('data-status-banner', state)
      expect(container.querySelector('[data-banner-pill]'), decision).toHaveTextContent(pill)
      unmount()
    }
  })

  it('puts the composite first when the gate has one, still at most four facts', () => {
    const release = makeRelease({ blockers: [blocker('warn', 'Composite under cap')] })
    const { container } = render(<ReleaseHealthBanner highlighted={release} inProgressReleases={[release]} releases={[release]} />)
    expect(facts(container)).toEqual(['Composite pass rate 92.0%', 'Open blockers 1', 'Ready to ship 0', 'Blocked 0'])
  })

  it('"Open" opens the release that ships next; with no opener (its own page) there is no action', () => {
    const { next, all } = project()
    const onOpen = vi.fn()
    const { unmount } = render(<ReleaseHealthBanner highlighted={next} inProgressReleases={[next]} releases={all} onOpen={onOpen} />)
    fireEvent.click(screen.getByRole('button', { name: 'Open 2026.11 →' }))
    expect(onOpen).toHaveBeenCalledWith('rel-next')
    unmount()
    render(<ReleaseHealthBanner highlighted={next} inProgressReleases={[next]} releases={all} />)
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('says so when no release is in flight, with the counts still counted', () => {
    const shipped = makeRelease({ stage: 'released', gate: { ...NOT_EVALUATED } })
    const { container } = render(<ReleaseHealthBanner highlighted={null} inProgressReleases={[]} releases={[shipped]} />)
    expect(container.querySelector('[data-banner-pill]')).toHaveTextContent('NOTHING IN FLIGHT')
    expect(
      screen.getByText('No release in flight — create a new release or move a Planning release into progress.'),
    ).toBeInTheDocument()
    expect(facts(container)).toEqual(['Open blockers 0', 'Ready to ship 0', 'Blocked 0', 'Released · 30d 0'])
  })
})

describe('ReleaseHealthDetails — the rest, collapsed under the list', () => {
  beforeEach(() => localStorage.clear())

  it('is closed by default, its summary keeping the two counts the banner does not show', () => {
    const { next, all } = project()
    const { container } = render(<ReleaseHealthDetails highlighted={next} releases={all} />)
    const toggle = screen.getByRole('button', { name: /Release health · this week/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle).toHaveTextContent('2 in progress · 1 released in 30 days')
    // Closed: nothing inside is drawn.
    expect(container.querySelector('[data-release-health-details]')).toBeNull()
  })

  it('opens to the gate lede, the top three blockers and the four count cards with their sub-lines', () => {
    const next = makeRelease({
      name: 'Checkout',
      blockers: [
        blocker('red', 'Regression failed', 'Regression'),
        blocker('warn', 'Flake rate 2.1% over cap', 'flake budget'),
        blocker('resolved', 'Coverage restored'),
        blocker('red', 'A fourth blocker'),
      ],
    })
    render(<ReleaseHealthDetails highlighted={next} releases={[next]} />)
    fireEvent.click(screen.getByRole('button', { name: /Release health · this week/ }))
    expect(screen.getByText('Composite pass rate 92.0% is under the 95% gate. 4 blockers across this release.')).toBeInTheDocument()
    const list = screen.getByRole('list', { name: 'Top blockers on Checkout' })
    expect(within(list).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Regression failed· Regression',
      'Flake rate 2.1% over cap· flake budget',
      'Coverage restored',
    ])
    for (const label of ['In progress', 'Ready to ship', 'Blocked', 'Released · 30d']) {
      expect(screen.getByText(label)).toBeInTheDocument()
    }
  })

  it('maps each blocker severity to its per-theme status token, not raw palette classes', () => {
    const release = makeRelease({
      blockers: [blocker('resolved', 'Coverage restored'), blocker('warn', 'Flake rate elevated'), blocker('red', 'Composite under gate')],
    })
    const { container } = render(<ReleaseHealthDetails highlighted={release} releases={[release]} />)
    fireEvent.click(screen.getByRole('button', { name: /Release health · this week/ }))
    const html = (container.querySelector('ul') as HTMLElement).innerHTML
    // resolved → passed, warn → broken, red → failed.
    expect(html).toContain('text-[var(--status-passed)]')
    expect(html).toContain('text-[var(--status-broken)]')
    expect(html).toContain('text-[var(--status-failed)]')
    // These raw palette strings are assertion guards (proving the classes are
    // absent), not UI — scoped-exempt from the design-audit token rule.
    /* eslint-disable no-restricted-syntax */
    expect(html).not.toContain('text-emerald-400')
    expect(html).not.toContain('text-amber-400')
    expect(html).not.toContain('text-red-400')
    /* eslint-enable no-restricted-syntax */
  })

  it('renders a blocker title as text, never as markup', () => {
    const release = makeRelease({ blockers: [blocker('red', '<img src=x onerror="window.__xss=1"> failed')] })
    const { container } = render(<ReleaseHealthDetails highlighted={release} releases={[release]} />)
    fireEvent.click(screen.getByRole('button', { name: /Release health · this week/ }))
    expect(container.querySelector('img')).toBeNull()
    expect(screen.getByText('<img src=x onerror="window.__xss=1"> failed')).toBeInTheDocument()
  })
})

describe('the release-health words and counts (health.ts)', () => {
  it('calls a not-yet-evaluated in-progress release in progress, never "in planning"', () => {
    const release = makeRelease({ name: 'Checkout', version: '2.5.0', gate: { ...NOT_EVALUATED } })
    expect(releaseHeadline(release)).toBe('Checkout (2.5.0) is in progress, not evaluated yet')
  })

  it('counts each number the way the strip does, and the banner states the same ones', () => {
    const { next, all } = project()
    expect(countReleases(all)).toEqual({ planning: 1, inProgress: 2, readyToShip: 1, blocked: 1, released30d: 1 })
    expect(releaseHealthFacts(next, all.filter((r) => r.stage === 'in_progress'), all).map((f) => [f.label, f.value])).toEqual([
      ['Open blockers', 1],
      ['Ready to ship', 1],
      ['Blocked', 1],
      ['Released · 30d', 1],
    ])
  })
})
