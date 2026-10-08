import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { cloneElement, isValidElement, useEffect, type ReactElement, type ReactNode } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { REDUCED_MOTION_QUERY } from '@/components/charts/motion'
import { releaseCouncilService, type ReleaseCouncilDecision } from '@/services/releaseCouncilService'
import type { Release } from '@/types/releases'

import ReleaseGatePage from './ReleaseGatePage'

const { mockProjectState } = vi.hoisted(() => ({
  mockProjectState: {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
  },
}))

vi.mock('@/hooks/useReleaseCouncil', () => ({
  useReleaseCouncil: vi.fn(),
}))

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) =>
    selector(mockProjectState)),
}))

// The risk ring is real Recharts; jsdom lays nothing out, so only the
// container is given the gauge's own 120 px box (everything else is real).
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactNode }) =>
      isValidElement(children)
        ? cloneElement(children as ReactElement<{ width?: number; height?: number }>, { width: 120, height: 120 })
        : null,
  }
})

// The catalogue's two requests go through `chartGet`; only it is replaced.
const chartGet = vi.hoisted(() => vi.fn())
vi.mock('@/services/chartApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/chartApi')>()),
  chartGet,
}))

const releaseList = vi.hoisted(() => ({ items: [] as Release[] }))
vi.mock('@/hooks/useReleases', () => ({
  useReleases: () => ({ data: { items: releaseList.items }, error: undefined, mutate: () => {} }),
}))

// The real Context group, wrapped in a spy so a test can read the props the
// page hands it (rule 4: never the verdict).
vi.mock('@/components/reports/catalogue/GateCatalogue', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/components/reports/catalogue/GateCatalogue')>()
  return { ...actual, default: vi.fn(actual.default) }
})

vi.mock('react-hot-toast', () => ({
  default: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

describe('ReleaseGatePage', () => {
  it('renders the release workflow strip and decision summary', async () => {
    const { useReleaseCouncil } = await import('@/hooks/useReleaseCouncil')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useReleaseCouncil as ReturnType<typeof vi.fn>).mockReturnValue({
      council: {
        run_id: 'run-1',
        recommendation: 'CONDITIONAL_GO',
        risk_score: 42,
        composite_risk: 41,
        dimension_scores: [],
        blocking_issues: ['Need one more smoke pass'],
        conditions_for_go: ['Approve smoke run'],
        reasoning: 'Policy accepted with conditions',
        score_model_version: 2,
        input_snapshot: null,
        cluster_insights: [],
        baseline_diff: null,
        open_defects_by_component: [],
        human_override: null,
        overridden_by: null,
        original_recommendation: null,
        original_risk_score: null,
        override_audit: [],
        pass_rate: 86.2,
        build_number: '42',
        policy_id: 'policy-1',
        policy_version: 3,
        policy_level: 'project',
        rule_evaluations: [],
      },
      isLoading: false,
      isError: false,
      refresh: vi.fn(),
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })

    render(
      <MemoryRouter initialEntries={['/release-gate/run-1']}>
        <Routes>
          <Route path="/release-gate" element={<ReleaseGatePage />} />
          <Route path="/release-gate/:runId" element={<ReleaseGatePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(screen.getAllByText(/CONDITIONAL GO/i).length).toBeGreaterThan(0)
    // P2 item 3: the decision flow is a collapsed "How this was decided"
    // disclosure at the bottom of the page — below the verdict card (and its
    // Override control) and the tabs (P4 item 6) — and renders nothing until
    // opened.
    const toggle = await screen.findByRole('button', { name: 'How this was decided' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText(/Release decision flow/i)).toBeNull()
    const override = screen.getByRole('button', { name: 'Override decision' })
    expect(override.compareDocumentPosition(toggle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const tabs = screen.getByRole('tablist', { name: 'Release gate sections' })
    expect(tabs.compareDocumentPosition(toggle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    fireEvent.click(toggle)
    expect(screen.getByText(/Release decision flow/i)).toBeInTheDocument()
    expect(screen.getAllByText(/Policy Evaluation/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Release Decision/i).length).toBeGreaterThan(0)
    // P2 item 9: the timeline no longer prints the literal word "compact"
    // beside its title (an implementation detail, not information; the prop
    // that drove it is gone).
    expect(screen.queryByText(/^compact$/i)).toBeNull()
  })

  it('stays stable when the release council transitions from loading to loaded', async () => {
    const { useReleaseCouncil } = await import('@/hooks/useReleaseCouncil')
    const { useRuns } = await import('@/hooks/useRuns')

    const state: {
      council: ReleaseCouncilDecision | null
      isLoading: boolean
      isError: boolean
      refresh: () => void
    } = {
      council: null,
      isLoading: true,
      isError: false,
      refresh: vi.fn(),
    }

    ;(useReleaseCouncil as ReturnType<typeof vi.fn>).mockImplementation(() => state)
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })

    const { rerender } = render(
      <MemoryRouter initialEntries={['/release-gate/run-1']}>
        <Routes>
          <Route path="/release-gate" element={<ReleaseGatePage />} />
          <Route path="/release-gate/:runId" element={<ReleaseGatePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(document.querySelector('.animate-spin') ?? screen.queryByText(/loading/i)).toBeTruthy()

    state.council = {
      run_id: 'run-1',
      recommendation: 'GO',
      risk_score: 12,
      composite_risk: 12,
      dimension_scores: [],
      blocking_issues: [],
      conditions_for_go: [],
      reasoning: 'Low risk and healthy baseline.',
      score_model_version: 2,
      input_snapshot: null,
      cluster_insights: [],
      baseline_diff: null,
      open_defects_by_component: [],
      human_override: null,
      overridden_by: null,
      original_recommendation: null,
      original_risk_score: null,
      override_audit: [],
      pass_rate: 98.2,
      build_number: '43',
      policy_id: 'policy-1',
      policy_version: 3,
      policy_level: 'project',
      rule_evaluations: [],
    }
    state.isLoading = false

    rerender(
      <MemoryRouter initialEntries={['/release-gate/run-1']}>
        <Routes>
          <Route path="/release-gate" element={<ReleaseGatePage />} />
          <Route path="/release-gate/:runId" element={<ReleaseGatePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('button', { name: 'How this was decided' })).toBeInTheDocument()
    expect(screen.getAllByText(/^GO$/i).length).toBeGreaterThan(0)
  })

  it('returns to the release gate overview when the project changes', async () => {
    const { useReleaseCouncil } = await import('@/hooks/useReleaseCouncil')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useReleaseCouncil as ReturnType<typeof vi.fn>).mockReturnValue({
      council: {
        run_id: 'run-1',
        recommendation: 'NO_GO',
        risk_score: 78,
        composite_risk: 78,
        dimension_scores: [],
        blocking_issues: ['Database saturation'],
        conditions_for_go: [],
        reasoning: 'Release blocked by critical risk.',
        score_model_version: 2,
        input_snapshot: null,
        cluster_insights: [],
        baseline_diff: null,
        open_defects_by_component: [],
        human_override: null,
        overridden_by: null,
        original_recommendation: null,
        original_risk_score: null,
        override_audit: [],
        pass_rate: 72.4,
        build_number: '42',
        policy_id: 'policy-1',
        policy_version: 3,
        policy_level: 'project',
        rule_evaluations: [],
      },
      isLoading: false,
      isError: false,
      refresh: vi.fn(),
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })

    const { rerender } = render(
      <MemoryRouter initialEntries={['/release-gate/run-1']}>
        <Routes>
          <Route path="/release-gate" element={<ReleaseGatePage />} />
          <Route path="/release-gate/:runId" element={<ReleaseGatePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/NO GO/i)).toBeInTheDocument()

    mockProjectState.activeProjectId = 'proj-2'
    mockProjectState.activeProject = { id: 'proj-2', name: 'Project Two' }

    rerender(
      <MemoryRouter initialEntries={['/release-gate/run-1']}>
        <Routes>
          <Route path="/release-gate" element={<ReleaseGatePage />} />
          <Route path="/release-gate/:runId" element={<ReleaseGatePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/No run selected/i)).toBeInTheDocument()
  })
})

// ── Which run /release-gate opens (browser E2E pass, 2026-10-08) ──────────
//
// It opened the project's newest run, and the newest was still running: its
// pass rate is null until ingestion finishes it, and the gate graded that as
// "NO GO, pass rate 0.0%, raise it to at least 90% (currently 0.0%)".

describe('ReleaseGatePage — the run it opens', () => {
  function Where() {
    return <p data-testid="where">{useLocation().pathname}</p>
  }

  async function openGate(items: { id: string; status: string }[]) {
    mockProjectState.activeProjectId = 'proj-1'
    mockProjectState.activeProject = { id: 'proj-1', name: 'Project One' }
    const { useReleaseCouncil } = await import('@/hooks/useReleaseCouncil')
    const { useRuns } = await import('@/hooks/useRuns')
    ;(useReleaseCouncil as ReturnType<typeof vi.fn>).mockReturnValue({
      council: null, isLoading: false, isError: false, refresh: vi.fn(),
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items } })
    render(
      <MemoryRouter initialEntries={['/release-gate']}>
        <Routes>
          <Route path="/release-gate" element={<ReleaseGatePage />} />
          <Route path="/release-gate/:runId" element={<Where />} />
        </Routes>
      </MemoryRouter>,
    )
    return screen.findByTestId('where')
  }

  it('skips a run still in progress for the newest finished one', async () => {
    const where = await openGate([
      { id: 'run-live', status: 'in_progress' },
      { id: 'run-done', status: 'failed' },
    ])
    expect(where).toHaveTextContent('/release-gate/run-done')
  })

  it('opens the newest when every recent run is still running', async () => {
    const where = await openGate([
      { id: 'run-a', status: 'IN_PROGRESS' },
      { id: 'run-b', status: 'running' },
    ])
    expect(where).toHaveTextContent('/release-gate/run-a')
  })

  it('a run with nothing to grade reads as pending, with no 0% and no ring', async () => {
    await renderGate(flooredDecision({
      risk_score: 0,
      composite_risk: null,
      dimension_scores: [],
      conditions_for_go: ['Wait for the run to finish: it is still in progress.'],
      reasoning: 'Not graded: the run is still in progress, so it has no pass rate yet.',
      input_snapshot: { synthesized: true, pass_rate: null, verdict_driver: 'in_progress' },
      pass_rate: null,
      synthesized: true,
    }))
    expect(screen.getByText('PENDING')).toBeInTheDocument()
    expect(screen.getByText(/No test evidence yet/)).toBeInTheDocument()
    // On the verdict card and in Why.
    expect(screen.getAllByText('Wait for the run to finish: it is still in progress.').length).toBeGreaterThan(0)
    expect(screen.queryByText(/Pass rate:/)).toBeNull()
    expect(screen.queryByText(/0\.0%/)).toBeNull()
    expect(screen.queryByRole('meter')).toBeNull()
  })
})

// ── The gauge vs the dimension breakdown ───────────────────────────────────
//
// Measured live 2026-08-16 on build ui-6 (pass rate 44.4%):
//
//     RiskGauge              60
//     Risk Dimension Breakdown   17/100   (weighted contributions sum to 16.67)
//
// Both styled as a risk out of 100, on one page, with nothing connecting them.
// The verdict is right — a pass-rate hard floor raises the composite to 60
// without touching any dimension — but the page never said so. The backend has
// published `input_snapshot.verdict_driver = "pass_rate_floor"` all along and
// no code read it: the value-nothing-consumes pattern this repo keeps finding.

function flooredDecision(
  overrides: Partial<ReleaseCouncilDecision> = {},
): ReleaseCouncilDecision {
  return {
    run_id: 'run-1',
    recommendation: 'NO_GO',
    // Floored: not the weighted total of the dimensions below.
    risk_score: 60,
    composite_risk: 60,
    dimension_scores: [
      { name: 'reproducibility', label: 'Reproducibility', score: 55.6, weight: 0.15, contribution: 8.34 },
      { name: 'blast_radius', label: 'Blast Radius', score: 22.2, weight: 0.15, contribution: 3.33 },
      { name: 'diagnosis_confidence', label: 'Diagnosis Confidence', score: 100, weight: 0.05, contribution: 5.0 },
    ],
    blocking_issues: [],
    conditions_for_go: [],
    reasoning: 'Quick-look decision derived from this run aggregates',
    score_model_version: 1,
    input_snapshot: { verdict_driver: 'pass_rate_floor', no_go_floor_pct: 63.0 },
    cluster_insights: [],
    baseline_diff: null,
    open_defects_by_component: [],
    human_override: null,
    overridden_by: null,
    original_recommendation: null,
    original_risk_score: null,
    override_audit: [],
    pass_rate: 44.4,
    build_number: 'ui-6',
    policy_id: null,
    policy_version: null,
    policy_level: 'hardcoded',
    rule_evaluations: [],
    ...overrides,
  } as ReleaseCouncilDecision
}

async function renderGate(decision: ReleaseCouncilDecision) {
  const { useReleaseCouncil } = await import('@/hooks/useReleaseCouncil')
  const { useRuns } = await import('@/hooks/useRuns')
  ;(useReleaseCouncil as ReturnType<typeof vi.fn>).mockReturnValue({
    council: decision, isLoading: false, isError: false, refresh: vi.fn(),
  })
  ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })
  render(
    <MemoryRouter initialEntries={['/release-gate/run-1']}>
      <Routes>
        <Route path="/release-gate/:runId" element={<ReleaseGatePage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('ReleaseGatePage — the risk score explains itself', () => {
  it('discloses that the score came from the pass-rate floor', async () => {
    await renderGate(flooredDecision())
    expect(await screen.findByText(/raised to the NO-GO floor/i)).toBeInTheDocument()
  })

  it('states the floor percentage it actually applied', async () => {
    await renderGate(flooredDecision())
    expect(await screen.findByText(/63%/)).toBeInTheDocument()
  })

  it('stays silent when the score really is the dimension total', async () => {
    // The disclosure must not become permanent furniture — a reader who sees it
    // on every verdict stops reading it.
    await renderGate(flooredDecision({
      input_snapshot: { verdict_driver: 'composite_risk' },
      risk_score: 17,
      composite_risk: 17,
      recommendation: 'GO',
      pass_rate: 96.0,
    }))
    expect(await screen.findByText(/Build ui-6 — AI-powered/)).toBeInTheDocument()
    expect(screen.queryByText(/raised to the NO-GO floor/i)).not.toBeInTheDocument()
  })

  it('does not present the dimension total as the verdict risk score', async () => {
    await renderGate(flooredDecision())
    // The breakdown header used to read a bare "17/100" beside a gauge of 60.
    expect(await screen.findByText(/17\/100 weighted/)).toBeInTheDocument()
  })
})

describe('ReleaseGatePage — the risk ring (OD-6)', () => {
  const arc = () => document.querySelector('.recharts-radial-bar-sector')
  // Reduced motion, as the production visual specs run: the arc is drawn at
  // its value on the first frame instead of animating up from 0.
  const realMatchMedia = window.matchMedia
  beforeEach(() => {
    window.matchMedia = ((query: string) => ({
      matches: query === REDUCED_MOTION_QUERY,
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
    })) as unknown as typeof window.matchMedia
  })
  afterEach(() => {
    window.matchMedia = realMatchMedia
  })

  it('is a named meter reading the verdict score, in theme tokens', async () => {
    await renderGate(flooredDecision())
    const ring = await screen.findByRole('meter', { name: 'Risk Score' })
    expect(ring).toHaveAttribute('aria-valuenow', '60')
    expect(ring).toHaveAttribute('aria-valuetext', '60')
    expect(within(ring).getByText('60').className).toContain('text-[var(--color-text)]')
    // The old arc: a fixed slate track and a literally white number. Every
    // fill and stroke is a theme token now (the track's edge included: the
    // kit's 3:1 non-text edge, R2 F9), never a colour literal.
    expect(ring.innerHTML).not.toMatch(/(fill|stroke)="(#|rgb|white|black|slate)/)
    for (const el of ring.querySelectorAll('[stroke]')) {
      if (el.getAttribute('stroke') !== 'none') expect(el.getAttribute('stroke')).toMatch(/var\(--/)
    }
    expect(ring.querySelector('.recharts-radial-bar-background-sector')).toHaveAttribute('fill', 'var(--chart-grid)')
    // No Recharts application layer: the ring is the meter.
    expect(ring.querySelector('[role="application"]')).toBeNull()
  })

  it('lower is better: 60 sits in the 40-70 warning band (amber)', async () => {
    await renderGate(flooredDecision())
    await screen.findByRole('meter', { name: 'Risk Score' })
    // 60 is in the 40-70 warning band.
    expect(arc()).toHaveAttribute('fill', 'var(--status-broken)')
  })

  it.each([
    [85, 'var(--status-failed)'],
    [70, 'var(--status-failed)'],
    [39, 'var(--status-passed)'],
  ])('colours a risk of %s with %s', async (score, fill) => {
    await renderGate(flooredDecision({ risk_score: score, composite_risk: score, input_snapshot: {} }))
    await screen.findByRole('meter', { name: 'Risk Score' })
    expect(arc()).toHaveAttribute('fill', fill)
  })
})

// ── The catalogue's Context group (VIZ-408, plan 2.5) ───────────────────────
//
// The verdict is STORED; the charts only explain it. These tests hold the page
// to that: the group sits after the evidence and outside the recommendation
// card, it is handed nothing that names the verdict, and drawing it (with data
// that says the opposite of the verdict) moves nothing in the card.

describe('ReleaseGatePage — the catalogue Context group', () => {
  const META = {
    schema_version: 1,
    scope: {
      projects: [{ id: 'proj-1', name: 'Project One' }],
      releases: [],
      suites: [],
      window: { from: '2026-07-01', to: '2026-09-28', days: 90, timezone: 'UTC' },
    },
    totals: { matched_runs: 2, total_runs: 2, matched_executions: 20, total_executions: 20 },
    pass_rate_basis: 'executions',
    ignored_filters: [],
    truncated: false,
    truncated_total: null,
    measured: true,
    reason: null,
    includes_in_progress: 0,
    partial_day: null,
    generated_at: '2026-09-28T09:30:00Z',
    as_of: '2026-09-28T09:30:00Z',
  }

  /** Two releases, every day at `rate` percent. */
  const releasesAt = (rate: number) => ({
    meta: META,
    series: {
      kind: 'series',
      dimensions: ['day', 'release'],
      x_type: 'time',
      series: ['rel-a', 'rel-b'].map((key) => ({
        key,
        label: key.toUpperCase(),
        points: ['2026-09-01', '2026-09-02', '2026-09-03'].map((x) => ({ x, y: rate, n: 10 })),
      })),
    },
  })

  const release = (id: string, releasedAt: string): Release => ({
    id,
    project_id: 'proj-1',
    name: id.toUpperCase(),
    version: null,
    description: null,
    status: 'released',
    planned_date: null,
    released_at: releasedAt,
    created_at: releasedAt,
    updated_at: releasedAt,
    phases: [],
  })

  const cluster = (id: string, label: string, size: number) => ({
    id,
    cluster_id: id,
    label,
    size,
    representative_error: null,
    member_test_ids: [],
    cohesion_score: null,
    criticality_level: null,
    dimension_scores: [],
  })
  const CLUSTERS = [cluster('c1', 'Timeouts', 7), cluster('c2', 'Auth', 2)]

  let chartRate = 100
  beforeEach(() => {
    mockProjectState.activeProjectId = 'proj-1'
    mockProjectState.activeProject = { id: 'proj-1', name: 'Project One' }
    releaseList.items = [release('rel-a', '2026-09-01T00:00:00Z'), release('rel-b', '2026-08-01T00:00:00Z')]
    chartRate = 100
    chartGet.mockReset()
    chartGet.mockImplementation((url: string) =>
      Promise.resolve({
        data: url.startsWith('/api/v1/runs/') ? { id: 'run-1', project_id: 'proj-1', release_id: 'rel-a' } : releasesAt(chartRate),
        requestId: 'req-1',
      }),
    )
  })
  // UX redesign P4 item 6: the group (and the cluster list below it) is the
  // Context tab's body, so every load here opens that tab.
  async function renderWith(decision: ReleaseCouncilDecision, waitForChart = true) {
    const { useReleaseCouncil } = await import('@/hooks/useReleaseCouncil')
    const { useRuns } = await import('@/hooks/useRuns')
    ;(useReleaseCouncil as ReturnType<typeof vi.fn>).mockReturnValue({
      council: decision, isLoading: false, isError: false, refresh: vi.fn(),
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })
    render(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <MemoryRouter initialEntries={['/release-gate/run-1?tab=context']}>
          <Routes>
            <Route path="/release-gate/:runId" element={<ReleaseGatePage />} />
          </Routes>
        </MemoryRouter>
      </SWRConfig>,
    )
    await screen.findByRole('meter', { name: 'Risk Score' })
    if (waitForChart) {
      // The group is its own lazy chunk: the first import can take a moment, and
      // more than 5 s under the full suite with coverage (it timed out twice there).
      await waitFor(
        () => expect(document.querySelector('[data-catalogue-section="gate-releases"] [data-chart-frame] svg')).toBeTruthy(),
        { timeout: 10_000 },
      )
    }
  }

  const card = () => screen.getByText('Recommendation').closest('.card') as HTMLElement
  const group = () => document.querySelector('[data-catalogue-section="gate-context"]') as HTMLElement | null
  /** Markup with per-render ids (React's, Recharts') made equal. */
  const stableMarkup = (el: HTMLElement) =>
    el.outerHTML.replace(/«[^»]*»|:r[0-9a-z]+:|_r_[0-9a-z]+_/g, '#id').replace(/recharts\d+-/g, 'recharts#-')

  /** What a reader takes from the verdict: its words, the ring's reading, the card itself. */
  const verdictReading = () => ({
    words: card().textContent,
    ring: screen.getByRole('meter', { name: 'Risk Score' }).getAttribute('aria-valuenow'),
    markup: stableMarkup(card()),
  })

  it('the group comes AFTER the recommendation card (and its blockers) and the tab bar, outside the card, above the cluster list', async () => {
    await renderWith(flooredDecision({ cluster_insights: CLUSTERS, blocking_issues: ['Checkout down'] }))
    const section = group() as HTMLElement
    expect(section).toBeTruthy()
    expect(card().contains(section)).toBe(false)
    expect(section.contains(card())).toBe(false)
    const follows = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
    expect(follows(card(), section)).toBe(true)
    // The evidence the verdict stands on is in the card, above the charts.
    expect(within(screen.getByRole('list', { name: 'Top blockers' })).getByText('Checkout down')).toBeInTheDocument()
    expect(follows(screen.getByRole('list', { name: 'Top blockers' }), section)).toBe(true)
    expect(follows(screen.getByRole('tablist', { name: 'Release gate sections' }), section)).toBe(true)
    expect(follows(section, screen.getByRole('heading', { name: 'Linked Failure Clusters' }))).toBe(true)
    // The cluster share, from the STORED clusters, says so.
    expect(section.querySelector('[data-catalogue-section="gate-clusters"] [data-gate-caption="stored"]')?.textContent).toMatch(
      /recorded with the decision for build ui-6/,
    )
    // The live chart says the verdict is not computed from it.
    expect(section.querySelector('[data-gate-caption="live"]')?.textContent).toMatch(
      /stored decision for build ui-6; it is not computed from this chart/,
    )
  })

  it('hands the group the run, the build and the stored clusters — and nothing that names the verdict', async () => {
    const GateCatalogue = (await import('@/components/reports/catalogue/GateCatalogue'))
      .default as unknown as ReturnType<typeof vi.fn>
    GateCatalogue.mockClear()
    await renderWith(flooredDecision({ cluster_insights: CLUSTERS }))
    const { calls } = GateCatalogue.mock
    const props = calls[calls.length - 1]?.[0] as Record<string, unknown>
    expect(Object.keys(props).sort()).toEqual(['build', 'clusters', 'runId'])
    expect(props).toEqual({ runId: 'run-1', build: 'ui-6', clusters: CLUSTERS })
  })

  it('names the run by its id in the captions when the decision has no build number', async () => {
    await renderWith(flooredDecision({ build_number: null }))
    expect(document.querySelector('[data-gate-caption="live"]')?.textContent).toMatch(/stored decision for build run-1;/)
  })

  it.each([
    ['a NO_GO verdict beside releases at 100%', 'NO_GO', 60, 100],
    ['a GO verdict beside releases at 0%', 'GO', 12, 0],
  ] as const)('verdict integrity: %s — card and ring identical with and without the group', async (_name, recommendation, risk, rate) => {
    const decision = flooredDecision({
      recommendation,
      risk_score: risk,
      composite_risk: risk,
      input_snapshot: {},
      cluster_insights: CLUSTERS,
    })
    chartRate = rate
    // Without: the group draws nothing (its chunk answered with an empty section).
    const GateCatalogue = (await import('@/components/reports/catalogue/GateCatalogue'))
      .default as unknown as ReturnType<typeof vi.fn>
    GateCatalogue.mockImplementation(() => null)
    let off: ReturnType<typeof verdictReading>
    try {
      await renderWith(decision, false)
      off = verdictReading()
    } finally {
      const actual = await vi.importActual<typeof import('@/components/reports/catalogue/GateCatalogue')>(
        '@/components/reports/catalogue/GateCatalogue',
      )
      GateCatalogue.mockImplementation(actual.default)
    }
    cleanup()
    await renderWith(decision)
    const on = verdictReading()
    expect(on).toEqual(off)
    expect(on.ring).toBe(String(risk))
    expect(on.words).toContain(recommendation === 'GO' ? 'GO' : 'NO GO')
  })

  it('the group is the same markup whatever the verdict: nothing in it is tinted by the decision', async () => {
    const markupFor = async (recommendation: 'GO' | 'NO_GO', risk: number) => {
      await renderWith(
        flooredDecision({ recommendation, risk_score: risk, composite_risk: risk, input_snapshot: {}, cluster_insights: CLUSTERS }),
      )
      const html = stableMarkup(group() as HTMLElement)
      cleanup()
      return html
    }
    const noGo = await markupFor('NO_GO', 85)
    const go = await markupFor('GO', 5)
    expect(go).toBe(noGo)
    expect(noGo).not.toMatch(/--status-/)
  })

  const follows = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
  /** The reasoning card's heading ("Decision Rationale", or "AI Reasoning" with an LLM configured). */
  const rationaleHeading = () => screen.getByRole('heading', { name: /^(Decision Rationale|AI Reasoning)$/ })
  const GateCatalogueSpy = async () =>
    (await import('@/components/reports/catalogue/GateCatalogue')).default as unknown as ReturnType<typeof vi.fn>

  // R1-1 (plan 2.5 rule 6): a section chunk that fails to load, or a section
  // that throws, was caught only by the ROUTE's boundary, which replaced the
  // whole page: the stored verdict, the ring and the override went with it.
  it('a Context group that throws is its own error card: the verdict, the ring and the override stay', async () => {
    const GateCatalogue = await GateCatalogueSpy()
    GateCatalogue.mockImplementation(() => {
      throw new Error('Failed to fetch dynamically imported module')
    })
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    try {
      await renderWith(flooredDecision({ cluster_insights: CLUSTERS, blocking_issues: ['Checkout down'] }), false)
      expect(await screen.findByText('Failed to load charts')).toBeInTheDocument()
      expect(card().textContent).toContain('NO GO')
      expect(screen.getByRole('meter', { name: 'Risk Score' })).toHaveAttribute('aria-valuenow', '60')
      expect(within(card()).getByRole('button', { name: 'Override decision' })).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: 'Linked Failure Clusters' })).toBeInTheDocument()
    } finally {
      spy.mockRestore()
      const actual = await vi.importActual<typeof import('@/components/reports/catalogue/GateCatalogue')>(
        '@/components/reports/catalogue/GateCatalogue',
      )
      GateCatalogue.mockImplementation(actual.default)
    }
  })

  // R2-8, re-cut by UX redesign P4 item 6: the verdict comes first; what
  // explains it (Decision Rationale, the Risk Dimension Breakdown the floor
  // note points at) is the Why tab, the default; the Context group is its own
  // tab, directly above the cluster list its share chart summarises, and is an
  // h3 like every other gate section (an h2 put the sections after it under
  // "Context" in the outline).
  it('Why (the default): the verdict, then Decision Rationale and the dimension breakdown — and no charts', async () => {
    const { useReleaseCouncil } = await import('@/hooks/useReleaseCouncil')
    const { useRuns } = await import('@/hooks/useRuns')
    ;(useReleaseCouncil as ReturnType<typeof vi.fn>).mockReturnValue({
      council: flooredDecision({ cluster_insights: CLUSTERS, blocking_issues: ['Checkout down'] }),
      isLoading: false, isError: false, refresh: vi.fn(),
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })
    render(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <MemoryRouter initialEntries={['/release-gate/run-1']}>
          <Routes>
            <Route path="/release-gate/:runId" element={<ReleaseGatePage />} />
          </Routes>
        </MemoryRouter>
      </SWRConfig>,
    )
    await screen.findByRole('meter', { name: 'Risk Score' })
    expect(screen.getByRole('tab', { name: 'Why' })).toHaveAttribute('aria-selected', 'true')
    const tabs = screen.getByRole('tablist', { name: 'Release gate sections' })
    const rationale = rationaleHeading()
    const breakdown = screen.getByText('Risk Dimension Breakdown')
    expect(follows(card(), tabs)).toBe(true)
    expect(follows(tabs, rationale)).toBe(true)
    expect(follows(rationale, breakdown)).toBe(true)
    expect(screen.getByRole('heading', { name: 'Blocking Issues' })).toBeInTheDocument()
    // The charts are another tab's: neither their placeholder nor a request.
    expect(group()).toBeNull()
    expect(document.querySelector('[data-gate-context-pending]')).toBeNull()
    expect(chartGet).not.toHaveBeenCalled()
  })

  it('Context: the verdict, the tab bar, THEN Context (an h3), then the cluster list', async () => {
    await renderWith(flooredDecision({ cluster_insights: CLUSTERS, blocking_issues: ['Checkout down'] }))
    expect(screen.getByRole('tab', { name: 'Context' })).toHaveAttribute('aria-selected', 'true')
    const section = group() as HTMLElement
    const heading = within(section).getByRole('heading', { name: 'Context' })
    expect(heading.tagName).toBe('H3')
    const tabs = screen.getByRole('tablist', { name: 'Release gate sections' })
    const clusters = screen.getByRole('heading', { name: 'Linked Failure Clusters' })
    const override = within(card()).getByRole('button', { name: 'Override decision' })
    expect(follows(override, tabs)).toBe(true)
    expect(follows(tabs, section)).toBe(true)
    expect(follows(section, clusters)).toBe(true)
    // Why's sections are not drawn under Context.
    expect(screen.queryByText('Risk Dimension Breakdown')).toBeNull()
    // Directly above: no other section heading between the group and the list.
    const between = Array.from(document.querySelectorAll('h2, h3')).filter(
      (h) => !section.contains(h) && follows(section, h) && follows(h, clusters),
    )
    expect(between).toEqual([])
  })

  // R1-7: while the group's chunk loads, the heading, the note and a box of
  // the group's height hold its place, so the cluster list below does not
  // jump down when it arrives.
  it.each([
    ['two charts', CLUSTERS, true],
    ['the release chart alone', [], false],
  ] as const)('while the chunk loads (%s): the heading, the note and a box of the section height', async (_name, clusters, both) => {
    const { contextBodyHeight } = await import('@/components/reports/catalogue/gateContextWords')
    const GateCatalogue = await GateCatalogueSpy()
    GateCatalogue.mockImplementation(() => {
      throw new Promise(() => undefined)
    })
    try {
      await renderWith(flooredDecision({ cluster_insights: [...clusters] }), false)
      const pending = document.querySelector('[data-gate-context-pending]') as HTMLElement
      expect(pending).not.toBeNull()
      expect(within(pending).getByRole('heading', { name: 'Context' }).tagName).toBe('H3')
      expect(within(pending).getByText(/These charts explain the evidence; they do not decide\./)).toBeInTheDocument()
      const box = pending.querySelector('[data-gate-context-box]') as HTMLElement
      expect(box).toHaveAttribute('aria-hidden', 'true')
      expect(box.style.minHeight).toBe(`${contextBodyHeight(both)}px`)
      expect(contextBodyHeight(true)).toBeGreaterThan(contextBodyHeight(false))
      expect(follows(card(), pending)).toBe(true)
      // The cluster list below waits under the held place, so it does not jump.
      if (both) expect(follows(pending, screen.getByRole('heading', { name: 'Linked Failure Clusters' }))).toBe(true)
    } finally {
      const actual = await vi.importActual<typeof import('@/components/reports/catalogue/GateCatalogue')>(
        '@/components/reports/catalogue/GateCatalogue',
      )
      GateCatalogue.mockImplementation(actual.default)
    }
  })

  it('stacks the recommendation row below sm and keeps the row from sm up', async () => {
    await renderWith(flooredDecision())
    const row = card().querySelector('[data-verdict-row]') as HTMLElement
    const classes = row.className.split(/\s+/)
    expect(classes).toEqual(expect.arrayContaining(['flex', 'flex-col', 'items-start', 'sm:flex-row', 'sm:items-center']))
  })
})

// ── UX redesign P4 item 6: verdict first, then Why · Context · History ──────
//
// The verdict card (GO / NO-GO, pass rate, the top three blockers, Override)
// is the page's primary content and comes before the tab bar and the
// pipeline disclosure; each tab is reachable by `?tab=`; every link that went
// to a page P4 merged into the run page now goes to its tab.

describe('ReleaseGatePage — verdict first (P4 item 6)', () => {
  const BLOCKERS = ['Checkout down', 'Payment gateway timeout', 'Session cookie not cleared', 'Login 500 on retry']

  function Where({ onChange }: { onChange: (where: string) => void }) {
    const location = useLocation()
    useEffect(() => onChange(location.pathname + location.search), [location, onChange])
    return null
  }

  async function renderAt(url: string, decision: ReleaseCouncilDecision, refresh = vi.fn()) {
    const { useReleaseCouncil } = await import('@/hooks/useReleaseCouncil')
    const { useRuns } = await import('@/hooks/useRuns')
    ;(useReleaseCouncil as ReturnType<typeof vi.fn>).mockReturnValue({
      council: decision, isLoading: false, isError: false, refresh,
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })
    const where = { current: '' }
    const track = (next: string) => { where.current = next }
    render(
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <MemoryRouter initialEntries={[url]}>
          <Where onChange={track} />
          <Routes>
            <Route path="/release-gate/:runId" element={<ReleaseGatePage />} />
            <Route path="/runs/:runId" element={<p>run page</p>} />
          </Routes>
        </MemoryRouter>
      </SWRConfig>,
    )
    await screen.findByRole('meter', { name: 'Risk Score' })
    return where
  }

  const follows = (a: Node, b: Node) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
  const primary = () => document.querySelector('[data-primary]') as HTMLElement

  beforeEach(() => {
    mockProjectState.activeProjectId = 'proj-1'
    mockProjectState.activeProject = { id: 'proj-1', name: 'Project One' }
    chartGet.mockReset()
  })

  it('the verdict card is the one primary element, before the tab bar and the pipeline disclosure', async () => {
    await renderAt('/release-gate/run-1', flooredDecision({ blocking_issues: BLOCKERS }))
    expect(document.querySelectorAll('[data-primary]')).toHaveLength(1)
    expect(primary()).toHaveTextContent('Recommendation')
    expect(primary()).toHaveTextContent('NO GO')
    expect(within(primary()).getByRole('meter', { name: 'Risk Score' })).toBeInTheDocument()
    expect(primary()).toHaveTextContent('Pass rate: 44.4%')
    const tablist = screen.getByRole('tablist', { name: 'Release gate sections' })
    const disclosure = screen.getByRole('button', { name: 'How this was decided' }).closest('[data-disclosure]') as HTMLElement
    expect(follows(primary(), tablist)).toBe(true)
    expect(follows(primary(), disclosure)).toBe(true)
    expect(follows(tablist, disclosure)).toBe(true)
    expect(within(tablist).getAllByRole('tab').map((t) => t.textContent)).toEqual(['Why', 'Context', 'History0'])
  })

  it('lists the top three blockers in the card, and "N more in Why" opens Why', async () => {
    const where = await renderAt('/release-gate/run-1?tab=history', flooredDecision({ blocking_issues: BLOCKERS }))
    const list = within(primary()).getByRole('list', { name: 'Top blockers' })
    expect(within(list).getAllByRole('listitem').map((li) => li.textContent?.replace(/^✕/, ''))).toEqual(BLOCKERS.slice(0, 3))
    fireEvent.click(within(primary()).getByRole('button', { name: '1 more in Why →' }))
    expect(screen.getByRole('tab', { name: 'Why' })).toHaveAttribute('aria-selected', 'true')
    expect(where.current).toBe('/release-gate/run-1')
    // Why has the full list.
    const why = screen.getByRole('heading', { name: 'Blocking Issues' }).closest('.card') as HTMLElement
    expect(within(why).getAllByRole('listitem')).toHaveLength(4)
  })

  it('a conditional verdict with no blockers lists its conditions for GO instead', async () => {
    await renderAt('/release-gate/run-1', flooredDecision({
      recommendation: 'CONDITIONAL_GO', blocking_issues: [], conditions_for_go: ['Approve smoke run'],
    }))
    expect(within(primary()).queryByRole('list', { name: 'Top blockers' })).toBeNull()
    expect(within(within(primary()).getByRole('list', { name: 'Conditions for GO' })).getByText('Approve smoke run')).toBeInTheDocument()
  })

  it('Override opens in the card and applies through the service', async () => {
    const override = vi.spyOn(releaseCouncilService, 'override').mockResolvedValue(flooredDecision())
    const refresh = vi.fn().mockResolvedValue(undefined)
    await renderAt('/release-gate/run-1', flooredDecision(), refresh)
    fireEvent.click(within(primary()).getByRole('button', { name: 'Override decision' }))
    expect(within(primary()).getByRole('heading', { name: 'QA Lead Override' })).toBeInTheDocument()
    fireEvent.click(within(primary()).getByRole('button', { name: 'GO' }))
    fireEvent.change(within(primary()).getByRole('textbox', { name: 'Reason for override' }), { target: { value: 'Known flake' } })
    fireEvent.click(within(primary()).getByRole('button', { name: 'Apply Override' }))
    await waitFor(() => expect(override).toHaveBeenCalledWith('run-1', 'GO', 'Known flake'))
    await waitFor(() => expect(refresh).toHaveBeenCalled())
    override.mockRestore()
  })

  it.each([
    ['why', 'Why', () => screen.getByRole('heading', { name: /^(Decision Rationale|AI Reasoning)$/ })],
    ['context', 'Context', () => document.querySelector('[data-tab-panel="context"]')],
    ['history', 'History', () => screen.getByText(/No overrides recorded for this decision/)],
  ] as const)('?tab=%s selects %s and renders its section', async (id, label, section) => {
    await renderAt(`/release-gate/run-1?tab=${id}`, flooredDecision())
    expect(screen.getByRole('tab', { name: new RegExp(`^${label}`) })).toHaveAttribute('aria-selected', 'true')
    expect(section()).toBeTruthy()
    for (const other of ['why', 'context', 'history'].filter((t) => t !== id)) {
      expect(document.querySelector(`[data-tab-panel="${other}"]`)).toBeNull()
    }
  })

  it('History lists every override with its reason', async () => {
    await renderAt('/release-gate/run-1?tab=history', flooredDecision({
      override_audit: [{
        timestamp: '2026-09-01T10:00:00Z', actor_name: 'Dana', before_recommendation: 'NO_GO',
        after_recommendation: 'GO', reason: 'Known infra flake',
      } as ReleaseCouncilDecision['override_audit'][number]],
    }))
    expect(screen.getByRole('tab', { name: 'History1' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('heading', { name: 'Override History (1)' })).toBeInTheDocument()
    expect(screen.getByText('Known infra flake')).toBeInTheDocument()
    expect(screen.getByText('by Dana')).toBeInTheDocument()
  })

  it("links to the run page's Analysis tab, never to the URLs P4 merged away", async () => {
    await renderAt('/release-gate/run-1?tab=context', flooredDecision({
      cluster_insights: [{
        id: 'c1', cluster_id: 'c1', label: 'Timeouts', size: 3, representative_error: null,
        member_test_ids: [], cohesion_score: null, criticality_level: null, dimension_scores: [],
      }] as unknown as ReleaseCouncilDecision['cluster_insights'],
    }))
    expect(screen.getByRole('link', { name: /Run analysis/ })).toHaveAttribute('href', '/runs/run-1?tab=analysis')
    expect(screen.getByRole('link', { name: 'Details →' })).toHaveAttribute('href', '/runs/run-1?tab=analysis')
    const hrefs = Array.from(document.querySelectorAll('a[href]')).map((a) => a.getAttribute('href') ?? '')
    expect(hrefs.filter((h) => /\/intelligence\b|\/deep-investigate\//.test(h))).toEqual([])
  })

  it("a quick-look decision sends \"Run deep investigation\" to the run's Analysis tab", async () => {
    const where = await renderAt('/release-gate/run-1', flooredDecision({ synthesized: true }))
    fireEvent.click(screen.getByRole('button', { name: 'Run deep investigation' }))
    expect(where.current).toBe('/runs/run-1?tab=analysis')
  })

  it('moves PDF and Share into the header overflow menu', async () => {
    await renderAt('/release-gate/run-1', flooredDecision())
    expect(screen.queryByRole('button', { name: /^PDF$/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    expect(screen.getByRole('menuitem', { name: /Export PDF/ })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /Share report/ })).toBeInTheDocument()
  })
})
