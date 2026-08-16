import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ValueMetrics } from '@/types/valueMetrics'
import OverviewPage from './OverviewPage'

vi.mock('@/hooks/useMetrics', () => {
  const d = () => ({ data: undefined, isLoading: false })
  return {
    useDashboardSummary:  vi.fn(d),
    useTrendData:         vi.fn(d),
    useFlakyTests:        vi.fn(d),
    useFailureCategories: vi.fn(d),
    useTopFailing:        vi.fn(d),
    useCoverage:          vi.fn(d),
    useDefects:           vi.fn(d),
    useSuiteDetail:       vi.fn(d),
    useAiSummary:         vi.fn(d),
  }
})
vi.mock('@/hooks/useSuiteOptions', () => ({
  useSuiteOptions: () => ({ options: [], isLoading: false }),
}))
// Eng-hours saved KPI (US-12.2): mutable state so tests can flip between
// available / unavailable / not-yet-loaded.
const valueKpiState: { metrics: ValueMetrics | undefined } = { metrics: undefined }
vi.mock('@/hooks/useValueMetrics', () => ({
  useValueMetricsKpi: () => valueKpiState,
}))
// KPI cards render only for widget ids in the active view, so tests that
// assert on a card must opt it in. Mutable so each test can choose.
const analyticsViewState: { widgetIds: string[] } = { widgetIds: [] }
vi.mock('@/hooks/useAnalyticsView', () => ({
  useAnalyticsView: () => ({
    instances: [], widgetIds: analyticsViewState.widgetIds,
    addInstance: vi.fn(), removeInstance: vi.fn(),
    save: vi.fn(), reset: vi.fn(), isDirty: false, savedViews: [],
    activeViewId: null, setActiveView: vi.fn(), deleteView: vi.fn(),
    updateInstance: vi.fn(), moveInstance: vi.fn(),
  }),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string; activeProject: { name: string } | null }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { name: 'Project One' } })),
}))

function hoursSavedMetrics(overrides: Partial<ValueMetrics> = {}): ValueMetrics {
  return {
    period_days: 30,
    project_id: 'proj-1',
    triage_time_saved_minutes: 0,
    triage_time_saved_hours: 0,
    defects_auto_grouped: 0,
    tests_grouped: 0,
    duplicate_tickets_avoided: 0,
    defects_promoted: 0,
    flaky_tests_identified: 0,
    quarantine_recommended: 0,
    risky_releases_blocked: 0,
    releases_conditional: 0,
    release_overrides: 0,
    intelligence_reports_generated: 0,
    available: true,
    insufficient_data_reason: null,
    headline: { hours_saved_30d: 42.5, fte_equivalent_30d: 0.8 },
    monthly: [],
    assumptions: {
      triage_minutes_per_failure: 15,
      blocked_run_wait_minutes: 30,
      defect_filing_minutes: 10,
    },
    assumptions_source: 'default',
    methodology_version: 1,
    ...overrides,
  }
}

function mockDashboardData(useDashboardSummary: unknown, useTrendData: unknown) {
  ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
    data: {
      release_readiness: 'GREEN',
      total_executions_7d: { value: 120 },
      avg_pass_rate_7d: { value: 91.5 },
      active_defects: { value: 4 },
      flaky_test_count: { value: 2 },
      new_failures_24h: { value: 3 },
      avg_duration_ms: { value: 180000 },
    },
    isLoading: false,
  })
  ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
    data: { data: [] },
    isLoading: false,
  })
}

describe('OverviewPage', () => {
  beforeEach(() => {
    valueKpiState.metrics = undefined
    analyticsViewState.widgetIds = []
  })

  // ── KPI trend units ──────────────────────────────────────────────────────
  //
  // Regression (homelab, 2026-08-16): `MetricCard.trend` is a RELATIVE
  // PERCENTAGE change vs the previous period — ((cur - prev) / prev) * 100 in
  // metrics_service — but the KPI rendered it bare. The live dashboard showed
  //     TOTAL EXECUTIONS  150   ▲ +400
  // where +400 means "quadrupled", not "four hundred more runs". A figure whose
  // unit is not shown is a figure the reader assigns the wrong unit to — the
  // same class as the run summary that stated counts which could not all be
  // true at once.

  it('renders a KPI trend as a percentage, not a bare count', async () => {
    analyticsViewState.widgetIds = ['total_executions_kpi', 'avg_pass_rate_kpi']
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        release_readiness: 'GREEN',
        total_executions_7d: { value: 150, trend: 400, trend_direction: 'up' },
        avg_pass_rate_7d: { value: 84.7, trend: -2.2, trend_direction: 'down' },
        active_defects: { value: 4 },
        flaky_test_count: { value: 2 },
        new_failures_24h: { value: 3 },
        avg_duration_ms: { value: 180000 },
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    // The +400 must carry its unit; without it this reads as 400 executions.
    // findAllByText, not findByText: the coverage micro-strip renders the same
    // trend with the same unit, so a singular query throws on two matches —
    // which would fail this test for the OPPOSITE of the reason it exists.
    expect((await screen.findAllByText(/\+400%/)).length).toBeGreaterThan(0)
    expect(screen.queryByText(/▲ \+400$/)).not.toBeInTheDocument()
    // Downward trends too — the sign is already in the number.
    expect(screen.getByText(/-2\.2%/)).toBeInTheDocument()
  })

  it('keeps the unit on a flat trend too', async () => {
    // Found by mutation: changing the flat branch from '0%' back to '0' passed
    // every other test here. An unchanged metric is still a percentage, and a
    // bare "0" beside a count reads as "zero runs", not "no change".
    analyticsViewState.widgetIds = ['total_executions_kpi']
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        release_readiness: 'GREEN',
        total_executions_7d: { value: 150, trend: null, trend_direction: 'flat' },
        avg_pass_rate_7d: { value: 84.7 },
        active_defects: { value: 4 },
        flaky_test_count: { value: 2 },
        new_failures_24h: { value: 3 },
        avg_duration_ms: { value: 180000 },
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await screen.findByText('Total executions')
    const badges = Array.from(document.querySelectorAll('span'))
      .map((e) => e.textContent?.trim() ?? '')
      .filter((t) => t.startsWith('▬'))
    expect(badges.length).toBeGreaterThan(0)
    for (const b of badges) expect(b).toMatch(/%/)
  })

  it('states what the trend is measured against', async () => {
    // A percentage with no baseline is still ambiguous: 400% of what, since when?
    analyticsViewState.widgetIds = ['total_executions_kpi']
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        release_readiness: 'GREEN',
        total_executions_7d: { value: 150, trend: 400, trend_direction: 'up' },
        avg_pass_rate_7d: { value: 84.7 },
        active_defects: { value: 4 },
        flaky_test_count: { value: 2 },
        new_failures_24h: { value: 3 },
        avg_duration_ms: { value: 180000 },
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    // The KPI badge is the one carrying the explanatory title; the micro-strip
    // renders the same percentage without one.
    const badges = await screen.findAllByText(/\+400%/)
    const titled = badges.filter((el) => el.getAttribute('title'))
    expect(titled.length).toBeGreaterThan(0)
    expect(titled[0]).toHaveAttribute('title', expect.stringMatching(/previous period/i))
  })

  it('renders the Eng-hours saved KPI card only when the hours-saved model is available', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)
    valueKpiState.metrics = hoursSavedMetrics()

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText('Eng-hours saved')).toBeInTheDocument()
    expect(screen.getByText(/42\.5/)).toBeInTheDocument()
    expect(screen.getByText(/View value metrics/i)).toBeInTheDocument()
  })

  it('omits the Eng-hours saved KPI card entirely when unavailable — no dash-card', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)
    valueKpiState.metrics = hoursSavedMetrics({
      available: false,
      insufficient_data_reason: 'Not enough ingested data yet.',
      headline: { hours_saved_30d: 0, fte_equivalent_30d: 0 },
    })

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await screen.findAllByText(/Quality workflow/i)
    expect(screen.queryByText('Eng-hours saved')).toBeNull()
    expect(screen.queryByText(/View value metrics/i)).toBeNull()
  })

  it('omits the Eng-hours saved KPI card while value metrics have not loaded', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)
    valueKpiState.metrics = undefined

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await screen.findAllByText(/Quality workflow/i)
    expect(screen.queryByText('Eng-hours saved')).toBeNull()
  })

  it('renders the quality workflow strip above dashboard metrics', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')

    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        release_readiness: 'GREEN',
        total_executions_7d: { value: 120 },
        avg_pass_rate_7d: { value: 91.5 },
        active_defects: { value: 4 },
        flaky_test_count: { value: 2 },
        new_failures_24h: { value: 3 },
        avg_duration_ms: { value: 180000 },
      },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        data: [
          { day: '2026-04-01', passed: 90, failed: 5, skipped: 2, broken: 0 },
          { day: '2026-04-02', passed: 91, failed: 4, skipped: 1, broken: 0 },
        ],
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    // ``Quality workflow`` appears in both the workflow strip and the
    // dashboard widget header, so use getAllByText for the presence check.
    expect((await screen.findAllByText(/Quality workflow/i)).length).toBeGreaterThan(0)
    expect(screen.getByText(/^Dashboard$/i)).toBeInTheDocument()
    // The page renders the verdict via ``gateLabel`` — ``GREEN`` readiness
    // maps to "Go". Match the rendered label rather than the raw backend
    // colour to stay aligned with the verdict-led redesign.
    expect(screen.getAllByText(/\bGo\b/).length).toBeGreaterThan(0)
  })

  it('normalizes legacy day-only trend points without logging a render error', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        data: [
          { day: '2026-04-01', passed: 90, failed: 5, skipped: 2, broken: 0 },
          { day: '2026-04-02', passed: 91, failed: 4, skipped: 1, broken: 0 },
        ],
      },
      isLoading: false,
    })
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findAllByText(/Quality workflow/i)).not.toHaveLength(0)
    expect(consoleError).not.toHaveBeenCalledWith(
      expect.stringContaining("Cannot read properties of undefined (reading 'length')"),
    )
    consoleError.mockRestore()
  })

  it('shows Pending readiness instead of RED when there are zero executions', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')

    // Backend can still return RED on an empty dataset (default thresholds
    // applied to zero data) — the UI must override the verdict because
    // "Critical issues must be resolved" is misleading when there's nothing
    // to assess.
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        release_readiness: 'RED',
        total_executions_7d: { value: 0 },
        avg_pass_rate_7d: { value: 0 },
        active_defects: { value: 0 },
        flaky_test_count: { value: 0 },
        new_failures_24h: { value: 0 },
        avg_duration_ms: { value: 0 },
      },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [] },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    // Zero-executions case must surface as the Pending verdict (via
    // ``mapReadinessToVerdict`` → ``gateLabel`` = "Pending"), not the
    // misleading "Critical issues must be resolved" copy.
    expect(await screen.findByText(/\bPending\b/)).toBeInTheDocument()
    expect(screen.queryByText(/Critical issues must be resolved/i)).toBeNull()
  })

  it('does not render fabricated cost/evidence/duration literals or dead CTAs', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')

    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        release_readiness: 'GREEN',
        total_executions_7d: { value: 120 },
        avg_pass_rate_7d: { value: 91.5 },
        active_defects: { value: 4 },
        flaky_test_count: { value: 2 },
        new_failures_24h: { value: 3 },
        avg_duration_ms: { value: 180000 },
      },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [{ date: '2026-04-02', passed: 91, failed: 4, skipped: 1, broken: 0, pass_rate: 94 }] },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await screen.findAllByText(/Quality workflow/i)
    // Fabricated ribbon literals must be gone (the dashboard has no real
    // per-run cost / evidence-count / stage-duration signal to report).
    expect(screen.queryByText(/\$0\.31/)).toBeNull()
    expect(screen.queryByText(/evidence items/i)).toBeNull()
    // The invented "readiness confidence %" is replaced by the real pass rate.
    expect(screen.queryByText(/Readiness confidence/i)).toBeNull()
    expect(screen.getAllByText(/^Pass rate$/i).length).toBeGreaterThan(0)
    // Dead CTAs (no-op handlers) are removed, not shipped as inert buttons.
    expect(screen.queryByRole('button', { name: /Run quality workflow/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /View evidence/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /Override gate/i })).toBeNull()
  })
})

/**
 * The blockers panel must describe the number it is actually showing.
 *
 * ``new_failures_24h`` is computed in ``metrics_service`` as a fixed
 * 24-hour count::
 *
 *     TestCase.status == FAILED AND TestCase.created_at >= now - 24h
 *
 * It ignores the dashboard's time-window selector entirely. Measured live
 * against a freshly-ingested run, the value is identical at every window::
 *
 *     days=1   new_failures_24h=3
 *     days=7   new_failures_24h=3
 *     days=30  new_failures_24h=3
 *     days=90  new_failures_24h=3
 *
 * Yet the panel described that one number three different ways in the same
 * box, two of them false (captured from the live page with a 7-day window)::
 *
 *     badge:  "3 new · 24h"                                     <- correct
 *     body:   "3 new failures in the window."                   <- 7 days, not 24 h
 *     footer: "Showing the 3 failures since the last green run" <- no green run involved
 *
 * "Since the last green run" is the most misleading: it names a
 * regression-since-green computation that does not exist anywhere in the
 * metric, and it would drive different triage than "failed in the last day".
 * The empty state carried the same claim.
 *
 * Fixed by making the copy match the metric — the badge and the KPI label
 * both already said 24 h, so the metric's intent was never in doubt.
 */
describe('OverviewPage — executions are not runs', () => {
  // Regression (homelab, 2026-08-16): `total_executions_7d` counts TEST
  // EXECUTIONS, but the page called them "runs" in five places, including the
  // release verdict's stated sample size. Measured live:
  //
  //     SAMPLE SIZE  702 runs / 30 days      <- 104 runs actually existed
  //     SAMPLE SIZE   60 runs / 30 days      <- a 6-run project, 60 executions
  //
  // A reader weighing a No-Go verdict was told the evidence base was ~7x
  // larger than it was. The MicroStrip compounded it by printing the relative
  // trend as an absolute: "702 runs · +680 this period" for +680%.

  beforeEach(() => {
    valueKpiState.metrics = undefined
    analyticsViewState.widgetIds = []
  })

  async function renderWithExecutions(value: number, trend: number | null = null) {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        release_readiness: 'GREEN',
        total_executions_7d: { value, trend, trend_direction: trend == null ? 'flat' : 'up' },
        avg_pass_rate_7d: { value: 100, trend: null, trend_direction: 'flat' },
        active_defects: { value: 0 },
        flaky_test_count: { value: 0 },
        new_failures_24h: { value: 0 },
        avg_duration_ms: { value: 1000 },
      },
      isLoading: false,
    })
    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  /** The verdict's "Sample size" card, as a single string. */
  function sampleSizeCardText(): string {
    const label = screen.getByText('Sample size')
    const card = label.parentElement
    if (!card) throw new Error('Sample size card has no container')
    return card.textContent ?? ''
  }

  it('does not describe the execution count as a run count', async () => {
    await renderWithExecutions(60)
    // Scoped to the card on purpose: a page-wide queryByText(/60 runs/) passes
    // whatever the card says, because the value and its unit are separate
    // elements. That version of this guard survived mutation.
    //
    // A plain substring check, not a regex: two successive attempts to write
    // /runs?/ landed a literal backspace and then a literal backslash in
    // the pattern. Both could never match, so `.not.toMatch` passed
    // unconditionally and the sibling assertion below was doing all the work.
    expect(sampleSizeCardText().toLowerCase()).not.toContain('run')
  })

  it('names the unit it is actually counting', async () => {
    await renderWithExecutions(60)
    const text = sampleSizeCardText()
    expect(text).toMatch(/60/)
    expect(text).toMatch(/execution/i)
  })

  it('renders the coverage trend as a percentage, not a count of runs', async () => {
    await renderWithExecutions(702, 680)
    // "+680 this period" reads as 680 more runs; it means the count grew 680%.
    expect(screen.queryByText(/\+680 this period/)).not.toBeInTheDocument()
    expect(screen.getByText(/\+680% this period/)).toBeInTheDocument()
  })
})

describe('OverviewPage — blockers panel describes its own metric', () => {
  async function renderWithFailures() {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)
    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('does not claim the 24h count covers "the window"', async () => {
    await renderWithFailures()
    await screen.findByText(/What's blocking release/i)
    expect(
      screen.queryByText(/new failures? in the window/i),
      'the panel says "in the window" for a value that ignores the window selector',
    ).toBeNull()
  })

  it('does not attribute the count to "the last green run"', async () => {
    await renderWithFailures()
    await screen.findByText(/What's blocking release/i)
    expect(
      screen.queryByText(/since the last green run/i),
      'the panel attributes a fixed 24h count to a green-run baseline that is ' +
        'not part of the computation',
    ).toBeNull()
  })

  it('still states the 24h window it actually measures', async () => {
    await renderWithFailures()
    await screen.findByText(/What's blocking release/i)
    const body = document.body.textContent ?? ''
    expect(body).toMatch(/24\s*h/i)
  })

  it('does not call an unresolved-failure release No-Go clear', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        release_readiness: 'RED',
        total_executions_7d: { value: 12 },
        avg_pass_rate_7d: { value: 75 },
        active_defects: { value: 2 },
        flaky_test_count: { value: 1 },
        new_failures_24h: { value: 0 },
        avg_duration_ms: { value: 120000 },
      },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [] },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText('Release remains blocked by unresolved failures.')).toBeInTheDocument()
    expect(screen.queryByText('Nothing is blocking release.')).toBeNull()
    expect(screen.getByText(/No new failures in the last 24 h; existing failures/i)).toBeInTheDocument()
  })
})
