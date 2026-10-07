import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { FIRST_RUN_DISMISS_KEY, firstRunDismissKey } from '@/components/onboarding/firstRunSteps'

import type { ValueMetrics } from '@/types/valueMetrics'
import OverviewPage from './OverviewPage'

// P1: the header's Views menu reads this page's saved views (none here).
vi.mock('@/services/savedViewsService', () => ({
  listSavedViews: vi.fn(async () => []),
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
}))
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
// The catalogue row (VIZ-408, mounted on every render since Phase D S1): the
// top bar's cached release list, and the server-backed section held in its
// loading state (its data is the section's own test).
vi.mock('@/hooks/useReleases', () => ({ useReleases: () => ({ data: { items: [] } }) }))
vi.mock('@/components/charts/chartCatalogSources', () => ({
  useCatalogChartData: () => ({ status: 'loading' }),
}))
// Eng-hours saved KPI (US-12.2): mutable state so tests can flip between
// available / unavailable / not-yet-loaded.
const valueKpiState: { metrics: ValueMetrics | undefined } = { metrics: undefined }
vi.mock('@/hooks/useValueMetrics', () => ({
  useValueMetricsKpi: () => valueKpiState,
}))
// P2: the page no longer reads the saved widget selection — the seven KPI
// cards always render. The hook stays mocked with an EMPTY selection, so a
// re-introduced `widgetIds` gate would hide every card and fail the KPI tests;
// one test below also hands it a narrow selection explicitly.
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

const runsState: {
  windowed: unknown[]
  newest: unknown[] | null
  /** The UNFILTERED "has this project ever had a run" probe. `undefined` here
   *  means "same as `newest`", which is what every pre-existing test assumes:
   *  with no release selected the two calls are identical and SWR serves them
   *  from one request. Set it to model a release filter that matches nothing
   *  on a project that does have runs. */
  everHad?: unknown[] | null
} = { windowed: [], newest: [] }
vi.mock('@/hooks/useRuns', () => ({
  // Three shapes now: the windowed list, the release-scoped newest run, and
  // the unfiltered lifetime probe. The first two are told apart by `size`,
  // exactly as the page does; the third by its explicit opt-out.
  useRuns: vi.fn((params?: { size?: number }, opts?: { ignoreGlobalRelease?: boolean }) => {
    let items: unknown[] | null
    if (opts?.ignoreGlobalRelease) {
      items = runsState.everHad !== undefined ? runsState.everHad : runsState.newest
    } else {
      items = params?.size === 1 ? runsState.newest : runsState.windowed
    }
    // `null` models a fetch SWR has not resolved yet (`data: undefined`). That
    // is not the same as an empty list, and the page must not read it as one.
    return { data: items === null ? undefined : { items } }
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
    // findAllByText, not findByText: a singular query throws if another
    // surface ever repeats the trend — which would fail this test for the
    // OPPOSITE of the reason it exists.
    expect((await screen.findAllByText(/\+400%/)).length).toBeGreaterThan(0)
    expect(screen.queryByText(/▲ \+400$/)).not.toBeInTheDocument()
    // Downward trends too — the sign is already in the number.
    expect(screen.getByText(/-2\.2%/)).toBeInTheDocument()
  })

  it('keeps the unit on a flat trend too', async () => {
    // Found by mutation: changing the flat branch from '0%' back to '0' passed
    // every other test here. An unchanged metric is still a percentage, and a
    // bare "0" beside a count reads as "zero runs", not "no change".
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

    // The KPI badge is the one carrying the explanatory title.
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

    await screen.findByRole('region', { name: 'Release readiness' })
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

    await screen.findByRole('region', { name: 'Release readiness' })
    expect(screen.queryByText('Eng-hours saved')).toBeNull()
  })

  it('leads with the readiness verdict, with no "Quality workflow" ribbon beside it (P2)', async () => {
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

    const verdictCard = await screen.findByRole('region', { name: 'Release readiness' })
    expect(screen.getByText(/^Dashboard$/i)).toBeInTheDocument()
    // The ribbon's four invented stages (and the heading that named it) are
    // gone, and the verdict no longer shares a two-column row with it.
    expect(screen.queryByText(/Quality workflow/i)).toBeNull()
    for (const stage of ['Quality Snapshot', 'Readiness Check', 'Trend Analysis', 'Action Focus']) {
      expect(screen.queryByRole('link', { name: `Open ${stage}` }), stage).toBeNull()
    }
    expect(verdictCard.parentElement).not.toHaveClass(
      'xl:[grid-template-columns:minmax(0,1fr)_minmax(0,1.55fr)]',
    )
    // It says what the verdict IS generated from: the readiness band.
    expect(within(verdictCard).getByText(/release-readiness band/)).toBeInTheDocument()
    // ...and the window it covers, not "generated just now" (the render time).
    expect(verdictCard).toHaveTextContent(/Verdict for the last \d+ days, from the release-readiness band/)
    expect(verdictCard.textContent).not.toMatch(/just now/)
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

    expect(await screen.findByRole('region', { name: 'Release readiness' })).toBeInTheDocument()
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

    await screen.findByRole('region', { name: 'Release readiness' })
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

  // ── P2: remove the noise ────────────────────────────────────────────────
  const ALL_KPIS = [
    'Total executions', 'Avg pass rate', 'Active defects', 'Flaky tests',
    'New failures · 24h', 'Infra-caused failures', 'Avg run duration',
  ]

  it('renders all seven KPI cards whatever the saved widget selection, and offers no Customize (P2)', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)
    // A saved selection of one card used to hide the other six, forever once
    // the picker that wrote it was gone.
    analyticsViewState.widgetIds = ['total_executions_kpi']
    try {
      render(
        <MemoryRouter initialEntries={['/overview']}>
          <Routes><Route path="/overview" element={<OverviewPage />} /></Routes>
        </MemoryRouter>,
      )
      await screen.findByRole('region', { name: 'Release readiness' })
      for (const label of ALL_KPIS) expect(screen.getByText(label), label).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /Customize/i })).toBeNull()
      expect(screen.queryByRole('dialog')).toBeNull()
    } finally {
      analyticsViewState.widgetIds = []
    }
  })

  it('drops the blocks that repeated the KPI row: reason cards and the coverage strip with its "—" MTTF tile (P2)', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)
    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes><Route path="/overview" element={<OverviewPage />} /></Routes>
      </MemoryRouter>,
    )
    const verdictCard = await screen.findByRole('region', { name: 'Release readiness' })
    // The verdict's three reason cards (each a KPI card again).
    expect(screen.queryByText('Sample size')).toBeNull()
    expect(within(verdictCard).queryByText('New failures · 24h')).toBeNull()
    // The coverage micro-strip: executions, avg duration (both KPI cards), an
    // inferred "last green run", and a Mean time to fix that was always "—".
    for (const gone of [/Automation coverage/i, /Last green run/i, /Mean time to fix/i]) {
      expect(screen.queryByText(gone), String(gone)).toBeNull()
    }
    expect(screen.queryByText(/needs ≥ 3 fixes/)).toBeNull()
    // Each KPI label once — no second block repeating it.
    expect(screen.getAllByText('Avg run duration')).toHaveLength(1)
  })

  it('shows no second "no executions" banner under the page when the window is empty (P2)', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        release_readiness: 'GREEN',
        total_executions_7d: { value: 0 },
        avg_pass_rate_7d: { value: 0 },
        active_defects: { value: 0 },
        flaky_test_count: { value: 0 },
        new_failures_24h: { value: 0 },
        avg_duration_ms: { value: 0 },
      },
      isLoading: false,
    })
    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes><Route path="/overview" element={<OverviewPage />} /></Routes>
      </MemoryRouter>,
    )
    // The verdict already says it (PENDING lede), and so does the empty-window notice.
    expect(await screen.findByText(/No test executions in the last \d+ days/)).toBeInTheDocument()
    expect(screen.queryByText(/readiness, KPIs, and blockers will assess once data lands/)).toBeNull()
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
  // larger than it was. The coverage strip (removed in P2) compounded it by
  // printing the relative trend as an absolute: "702 runs · +680 this period"
  // for +680%.

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

  /**
   * The verdict's statement of its sample, as a single string. P2 removed the
   * "Sample size" reason card (the Total executions KPI is the same number);
   * the verdict's lede is where the verdict itself states its sample now.
   */
  async function verdictSampleText(): Promise<string> {
    const verdictCard = await screen.findByRole('region', { name: 'Release readiness' })
    return within(verdictCard).getByText(/quality gates passed/).textContent ?? ''
  }

  it('does not describe the execution count as a run count', async () => {
    await renderWithExecutions(60)
    // Scoped to the verdict's sentence on purpose: a page-wide
    // queryByText(/60 runs/) passes whatever it says, because values and units
    // can sit in separate elements. That version of this guard survived mutation.
    //
    // A plain substring check, not a regex: two successive attempts to write
    // /runs?/ landed a literal backspace and then a literal backslash in
    // the pattern. Both could never match, so `.not.toMatch` passed
    // unconditionally and the sibling assertion below was doing all the work.
    expect((await verdictSampleText()).toLowerCase()).not.toContain('run')
  })

  it('names the unit it is actually counting', async () => {
    await renderWithExecutions(60)
    const text = await verdictSampleText()
    expect(text).toMatch(/60/)
    expect(text).toMatch(/execution/i)
  })

  it('renders the execution trend as a percentage, not a count of runs', async () => {
    await renderWithExecutions(702, 680)
    // "+680 this period" read as 680 more runs; it means the count grew 680%.
    // The strip that printed it is gone (P2); the KPI card carries the trend.
    expect(screen.queryByText(/this period/)).not.toBeInTheDocument()
    const card = screen.getByText('Total executions').closest('.rounded-xl') as HTMLElement
    expect(within(card).getByText(/\+680%/)).toBeInTheDocument()
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

describe('OverviewPage — a KPI caption must not deny its own value', () => {
  // Regression (homelab, 2026-08-16): the caption under each KPI fills the slot
  // the sparkline would occupy, and appears whenever the series has fewer than
  // two points. It was worded as a claim about the metric, so a project whose
  // runs all landed on one day read:
  //
  //     NEW FAILURES · 24H   23     14d · no failures recorded
  //     TOTAL EXECUTIONS     60     14d · awaiting runs
  //     AVG PASS RATE       57%     14d · need >= 2 runs      <- 6 runs existed
  //
  // The shortfall is days of history, not runs, and it explains a missing
  // trend line, not a missing metric.

  beforeEach(() => {
    valueKpiState.metrics = undefined
  })

  async function renderOneDayOfData() {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        release_readiness: 'RED',
        total_executions_7d: { value: 60 },
        avg_pass_rate_7d: { value: 57.4 },
        active_defects: { value: 0 },
        flaky_test_count: { value: 1 },
        new_failures_24h: { value: 23 },
        avg_duration_ms: { value: 0 },
      },
      isLoading: false,
    })
    // Six runs, all on one day — one trend point, so no sparkline anywhere.
    // The day is today: the payload only ever holds days of the window.
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        data: [
          { date: new Date().toISOString().slice(0, 10), passed: 31, failed: 17, skipped: 6, broken: 6, total: 60, pass_rate: 57.4 },
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
    await screen.findAllByText(/New failures/i)
  }

  it('does not report "no failures recorded" while showing 23 of them', async () => {
    await renderOneDayOfData()
    expect(screen.queryByText(/no failures recorded/i)).not.toBeInTheDocument()
  })

  it('does not report "awaiting runs" while showing 60 executions', async () => {
    await renderOneDayOfData()
    expect(screen.queryByText(/awaiting runs/i)).not.toBeInTheDocument()
  })

  it('does not blame a run shortfall for a history shortfall', async () => {
    await renderOneDayOfData()
    expect(screen.queryByText(/need ≥ 2 runs/i)).not.toBeInTheDocument()
  })

  it('explains the missing trend line instead, naming the days it has', async () => {
    await renderOneDayOfData()
    // The window store defaults to 30d.
    expect(screen.getAllByText(/1 of 30 days has data · no trend line/).length).toBe(3)
    // The old execution-trend chart blamed runs for the same shortfall.
    expect(screen.queryByText(/2 timed runs/)).not.toBeInTheDocument()
  })

  it('still says so plainly when the window really is empty', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        release_readiness: 'GREEN',
        total_executions_7d: { value: 0 },
        avg_pass_rate_7d: { value: 0 },
        active_defects: { value: 0 },
        flaky_test_count: { value: 0 },
        new_failures_24h: { value: 0 },
        avg_duration_ms: { value: 0 },
      },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({ data: { data: [] }, isLoading: false })
    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )
    expect((await screen.findAllByText(/30d · no executions recorded/)).length).toBeGreaterThan(0)
  })

  // ── Empty window vs empty project ────────────────────────────────────────
  //
  // The dashboard rendered 0/— across every KPI whenever the selected window
  // held no runs, with nothing to say why. Measured on the deployment: every
  // active project's newest run was 14-16 days old, so a 7- or 14-day window
  // was CORRECTLY empty and looked identical to an outage. The two empty
  // cases need different copy — telling a project with no data at all to
  // "widen the window" sends the reader round a loop that cannot help.

  function emptySummary(useDashboardSummary: unknown, useTrendData: unknown) {
    mockDashboardData(useDashboardSummary, useTrendData)
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        release_readiness: 'GREEN',
        total_executions_7d: { value: 0 },
        avg_pass_rate_7d: { value: 0 },
        active_defects: { value: 0 },
        flaky_test_count: { value: 0 },
        new_failures_24h: { value: 0 },
        avg_duration_ms: { value: 0 },
      },
      isLoading: false,
    })
  }

  function renderPage() {
    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('names the window and the data age when the window is empty', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    emptySummary(useDashboardSummary, useTrendData)
    const sixteenDaysAgo = new Date(Date.now() - 16 * 24 * 60 * 60 * 1000).toISOString()
    runsState.windowed = []
    runsState.newest = [{ id: 'r1', created_at: sixteenDaysAgo }]

    renderPage()

    const banner = await screen.findByTestId('overview-empty-window')
    // The window it searched, so the reader knows what to change.
    expect(banner.textContent).toMatch(/No runs in the last 30 days/i)
    // The age of the real data, so they know the data exists.
    expect(banner.textContent).toMatch(/16 days ago/i)
    // And a way out.
    expect(banner.textContent).toMatch(/Show last 90 days/i)
  })

  it('does NOT tell a project with no runs at all to widen the window', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    emptySummary(useDashboardSummary, useTrendData)
    runsState.windowed = []
    runsState.newest = []

    renderPage()

    const banner = await screen.findByTestId('overview-empty-window')
    expect(banner.textContent).toMatch(/No test runs yet/i)
    expect(banner.textContent).toMatch(/widening the time window will not help/i)
    expect(banner.textContent).not.toMatch(/Show last/i)
  })

  it('stays out of the way when the window has data', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)   // 120 executions
    runsState.windowed = [{ id: 'r1', created_at: new Date().toISOString() }]
    runsState.newest = [{ id: 'r1', created_at: new Date().toISOString() }]

    renderPage()

    // Anchor on the scope label, which renders in every state -- waiting on a
    // string that only appears in SOME states makes the absence check below
    // pass for the wrong reason (or fail, as it did first time).
    await screen.findAllByText(/Project One/i)
    expect(screen.queryByTestId('overview-empty-window')).not.toBeInTheDocument()
  })

  // ── F-067: the pass rate names its population ────────────────────────────
  //
  // /overview counts every execution (81.0% on the measured window) and the
  // Summary Report counts each distinct test once (83.3%). Same window, both
  // correct, and indistinguishable to a reader without the basis. The API has
  // published it on both surfaces since #778; the UI showed neither, so the
  // two figures still read as a contradiction.

  it('renders the pass-rate basis the API supplies', async () => {
    analyticsViewState.widgetIds = []
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        release_readiness: 'GREEN',
        total_executions_7d: { value: 120 },
        avg_pass_rate_7d: {
          value: 81.0, basis: 'executions', basis_label: 'per test execution',
        },
        active_defects: { value: 0 },
        flaky_test_count: { value: 0 },
        new_failures_24h: { value: 0 },
        avg_duration_ms: { value: 0 },
      },
      isLoading: false,
    })
    runsState.windowed = [{ id: 'r1', created_at: new Date().toISOString() }]
    runsState.newest = [{ id: 'r1', created_at: new Date().toISOString() }]

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes><Route path="/overview" element={<OverviewPage />} /></Routes>
      </MemoryRouter>,
    )

    expect((await screen.findAllByText(/per test execution/i)).length).toBeGreaterThan(0)
  })

  it('falls back to the old copy when the API omits the basis', async () => {
    // A cached pre-#778 payload has no basis. Rendering "undefined · 30d" would
    // be worse than the vague word it replaced.
    analyticsViewState.widgetIds = []
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)   // no basis in the mock
    runsState.windowed = [{ id: 'r1', created_at: new Date().toISOString() }]
    runsState.newest = [{ id: 'r1', created_at: new Date().toISOString() }]

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes><Route path="/overview" element={<OverviewPage />} /></Routes>
      </MemoryRouter>,
    )

    await screen.findAllByText(/Project One/i)
    expect(screen.queryByText(/undefined/i)).not.toBeInTheDocument()
    expect((await screen.findAllByText(/weighted/i)).length).toBeGreaterThan(0)
  })
})

// ── First-run guide is dismissed per project, not per browser ────────────────
//
// The guide shows only when THIS scope has no runs at all (an empty dashboard),
// but dismissal used to write one browser-wide flag. Dismissing it on the first
// empty project then suppressed the same first-run help on every genuinely new,
// still-empty project — the self-hoster who most needs it. Dismissal is now
// keyed on the active project id (`proj-1` in this suite's projectStore mock).
describe('OverviewPage — first-run guide dismissal is scoped to the project', () => {
  beforeEach(() => {
    valueKpiState.metrics = undefined
    analyticsViewState.widgetIds = []
    // Purge only this feature's keys; leave the rest of localStorage alone.
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const k = localStorage.key(i)
      if (k && k.startsWith(FIRST_RUN_DISMISS_KEY)) localStorage.removeItem(k)
    }
  })

  async function renderEmptyProject(
    newest: unknown[] | null = [],
    everHad?: unknown[] | null,
  ) {
    runsState.everHad = everHad
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        release_readiness: 'GREEN',
        total_executions_7d: { value: 0 },
        avg_pass_rate_7d: { value: 0 },
        active_defects: { value: 0 },
        flaky_test_count: { value: 0 },
        new_failures_24h: { value: 0 },
        avg_duration_ms: { value: 0 },
      },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({ data: { data: [] }, isLoading: false })
    runsState.windowed = []
    runsState.newest = newest
    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  it('shows the guide on an empty project and writes the per-project key on dismiss', async () => {
    await renderEmptyProject()
    expect(await screen.findByText(/Welcome to TestLookup/i)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /dismiss getting started/i }))

    // Gone immediately (reactive to the dismiss, no reload needed)…
    expect(screen.queryByText(/Welcome to TestLookup/i)).toBeNull()
    // …and persisted under the project-scoped key, not the bare browser-wide one.
    expect(localStorage.getItem(firstRunDismissKey('proj-1'))).toBe('1')
    expect(localStorage.getItem(FIRST_RUN_DISMISS_KEY)).toBeNull()
  })

  it('stays hidden when the project has runs OUTSIDE the selected window', async () => {
    // The reported bug: an established project whose last run predates the
    // window was greeted with "Welcome to TestLookup - no test runs here yet",
    // because the guide read the WINDOWED summary. Nothing about the project is
    // new; the user just picked 24h.
    await renderEmptyProject([{
      id: 'r1',
      created_at: new Date(Date.now() - 16 * 24 * 60 * 60 * 1000).toISOString(),
    }])

    // The window-empty banner is the right message here, and it appears...
    expect(await screen.findByTestId('overview-empty-window')).toBeInTheDocument()
    // ...instead of the onboarding guide, not stacked on top of it.
    expect(screen.queryByText(/Welcome to TestLookup/i)).toBeNull()
  })

  it('stays hidden when a RELEASE filter is empty but the project has runs', async () => {
    // Reported from the deployment against /overview?release=unattributed: a
    // project with runs, none of them unattributed, was greeted with "Welcome
    // to TestLookup - no test runs here yet" and the whole setup wizard,
    // telling an established team to run `make quickstart`.
    //
    // The same shape as the window case above, through a different door. That
    // one was fixed by asking for the newest run WITHOUT the day window; the
    // release axis then wired a global release filter through the very same
    // hook, so the lifetime probe silently became release-scoped again.
    await renderEmptyProject(
      // Nothing in the selected release...
      [],
      // ...but the project has a run.
      [{ id: 'r1', created_at: new Date().toISOString() }],
    )

    expect(screen.queryByText(/Welcome to TestLookup/i)).toBeNull()
  })

  it('still shows for a project with no runs in ANY release', async () => {
    // The control. A page that simply stopped rendering the guide would pass
    // the test above, and a genuinely new project would lose its onboarding.
    await renderEmptyProject([], [])

    expect(await screen.findByText(/Welcome to TestLookup/i)).toBeInTheDocument()
  })

  it('stays hidden while the lifetime run fetch is still loading', async () => {
    // `undefined` is "not answered yet", not "no runs". Reading it as empty
    // flashes the guide at every established project on first paint.
    await renderEmptyProject(null)

    await screen.findAllByText(/Project One/i)
    expect(screen.queryByText(/Welcome to TestLookup/i)).toBeNull()
  })

  it('stays hidden when THIS project was already dismissed', async () => {
    localStorage.setItem(firstRunDismissKey('proj-1'), '1')
    await renderEmptyProject()
    await screen.findAllByText(/Project One/i)
    expect(screen.queryByText(/Welcome to TestLookup/i)).toBeNull()
  })

  it('still shows when only a DIFFERENT project was dismissed', async () => {
    // Regression: onboarding another project must not hide this one's guide.
    localStorage.setItem(firstRunDismissKey('some-other-project'), '1')
    await renderEmptyProject()
    expect(await screen.findByText(/Welcome to TestLookup/i)).toBeInTheDocument()
  })

  it('ignores a legacy browser-wide dismiss flag', async () => {
    // A pre-scoping build wrote the bare key; it must no longer suppress the
    // guide, or the browser-wide bug survives the fix.
    localStorage.setItem(FIRST_RUN_DISMISS_KEY, '1')
    await renderEmptyProject()
    expect(await screen.findByText(/Welcome to TestLookup/i)).toBeInTheDocument()
  })
})

// ── Wave 2.5 (VIZ-104): the kit's chart, sparklines and meter ─────────────────
//
// The execution trend used to be a page-local Recharts area chart that mapped
// passed / failed / skipped only: a day's BROKEN executions were drawn nowhere
// and left out of the "Automation" total (owner decision OD-7: all four
// statuses, totals included). The pass-rate sparkline drew a day with nothing
// evaluated as 0 %. Both now go through the chart kit.
describe('OverviewPage — sparklines and meter on the chart kit', () => {
  const TREND = [
    { date: '2026-08-14', passed: 40, failed: 4, skipped: 2, broken: 6, total: 52, pass_rate: 80 },
    // Only skipped: nothing evaluated, so no pass rate — a gap, not 0 %.
    { date: '2026-08-15', passed: 0, failed: 0, skipped: 5, broken: 0, total: 5, pass_rate: 0 },
    { date: '2026-08-16', passed: 45, failed: 3, skipped: 1, broken: 2, total: 51, pass_rate: 90 },
  ]

  beforeEach(() => {
    valueKpiState.metrics = undefined
    // The window is the last 30 UTC days ending "today": pin today to the
    // fixture's newest day. Only `Date` is faked; the render's timers run.
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-08-16T12:00:00Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  async function renderWith(trend: unknown[], readiness = 'GREEN', executions = 108) {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        release_readiness: readiness,
        total_executions_7d: { value: executions },
        avg_pass_rate_7d: { value: 85.2 },
        active_defects: { value: 0 },
        flaky_test_count: { value: 0 },
        new_failures_24h: { value: 7 },
        avg_duration_ms: { value: 0 },
      },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({ data: { data: trend }, isLoading: false })
    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )
    await screen.findByText('Total executions')
  }

  it('breaks the pass-rate line on a day with nothing evaluated, rather than drawing 0 %', async () => {
    await renderWith(TREND)
    const line = screen.getByRole('img', { name: /^Avg pass rate per day, last 30 days:/ })
    // 2 evaluated days; the skips-only day and the 27 days with no runs are breaks.
    expect(line.getAttribute('aria-label')).toMatch(/: 2 points, .*min 80\.0%, max 90\.0%, 28 not measured$/)
  })

  it('draws the count sparklines from zero, and the pass rate on 0-100', async () => {
    await renderWith(TREND)
    // One point per day of the window: a day with no runs is a measured 0.
    expect(screen.getByRole('img', { name: /^Total executions per day, last 30 days: 30 points, .*min 0, max 52$/ })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /^New failures · 24h per day, last 30 days: 30 points/ })).toBeInTheDocument()
  })

  // R2 F1 / R1 F15: `/metrics/trends` sends only the days that had runs. The
  // sparklines used to bridge a silent week; the page's day window keeps it
  // (the catalogue's trend draws the same window: OverviewCatalogue.test).
  describe('a week with no runs keeps its place on the time axis', () => {
    // 30 UTC days ending 2026-08-16, with nothing on Aug 5-11.
    const HOLE = new Set(['2026-08-05', '2026-08-06', '2026-08-07', '2026-08-08', '2026-08-09', '2026-08-10', '2026-08-11'])
    const WINDOW = Array.from({ length: 30 }, (_, i) => new Date(Date.UTC(2026, 7, 16 - 29 + i)).toISOString().slice(0, 10))
    const HOLED = WINDOW.filter((day) => !HOLE.has(day)).map((date) => (
      { date, passed: 10, failed: 1, skipped: 1, broken: 0, total: 12, pass_rate: 90.91 }
    ))

    it('breaks the pass-rate line across the hole, and counts it as zero executions', async () => {
      await renderWith(HOLED)
      const rate = screen.getByRole('img', { name: /^Avg pass rate per day, last 30 days:/ })
      expect(rate.getAttribute('aria-label')).toMatch(/: 23 points, .*, 7 not measured$/)
      const total = screen.getByRole('img', { name: /^Total executions per day, last 30 days:/ })
      expect(total.getAttribute('aria-label')).toMatch(/: 30 points, .*min 0, max 12$/)
    })
  })

  it('reads the verdict’s pass rate as a meter, and PENDING as not measured', async () => {
    await renderWith(TREND)
    const meter = screen.getByRole('meter', { name: 'Pass rate' })
    expect(meter.getAttribute('aria-valuenow')).toBe('85')
    expect(meter.getAttribute('data-tone')).toBe('good')
  })

  it('draws no pass-rate bar at all while the verdict is PENDING', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { release_readiness: 'RED', total_executions_7d: { value: 0 }, avg_pass_rate_7d: { value: 0 } },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({ data: { data: [] }, isLoading: false })
    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )
    await screen.findAllByText(/\bPending\b/)
    expect(screen.queryByRole('meter', { name: 'Pass rate' })).toBeNull()
    expect(screen.getByRole('img', { name: 'Pass rate: not measured' })).toBeInTheDocument()
  })
})

describe('OverviewPage — the catalogue row (VIZ-408)', () => {
  const TREND = [
    { date: '2026-08-14', passed: 40, failed: 4, skipped: 2, broken: 6, total: 52, pass_rate: 80 },
    { date: '2026-08-15', passed: 0, failed: 0, skipped: 5, broken: 0, total: 5, pass_rate: 0 },
    { date: '2026-08-16', passed: 45, failed: 3, skipped: 1, broken: 2, total: 51, pass_rate: 90 },
  ]

  beforeEach(() => {
    valueKpiState.metrics = undefined
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-08-16T12:00:00Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  async function renderPage() {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        release_readiness: 'GREEN',
        total_executions_7d: { value: 108 },
        avg_pass_rate_7d: { value: 85.2 },
        active_defects: { value: 0 },
        flaky_test_count: { value: 0 },
        new_failures_24h: { value: 0 },
        avg_duration_ms: { value: 0 },
      },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({ data: { data: TREND }, isLoading: false })
    return render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )
  }

  const kpiValue = (label: string) =>
    (screen.getByText(label).closest('.rounded-xl') as HTMLElement).querySelector('.tabular-nums')?.textContent

  it('sizes the metric values on the type tokens of the same value (VIZ-106)', async () => {
    await renderPage()
    const value = (screen.getByText('Total executions').closest('.rounded-xl') as HTMLElement).querySelector('.tabular-nums') as HTMLElement
    expect(value.style.fontSize).toBe('var(--text-display-sm)')
    expect(value.className).not.toMatch(/text-\[26px\]/)
    // The verdict's pass rate: 26 px, the same token.
    const passRate = screen.getAllByText('85%').find((el) => el.nextElementSibling?.textContent === 'Pass rate') as HTMLElement
    expect(passRate.style.fontSize).toBe('var(--text-display-sm)')
    expect(passRate.className).not.toMatch(/text-\[26px\]/)
  })

  it('presentation mode (R2-13): no KPI value or change breaks across lines', async () => {
    await renderPage()
    const PRESENTING = '[[data-presentation=on]_&]:'
    // (The coverage strip this test also covered was removed in P2: each of its
    // tiles repeated a KPI card, or was always "—".)
    // The KPI cards: the value and its change each stay whole, and the change drops
    // under the value instead of breaking; the grid takes four columns in the room.
    const card = screen.getByText('Total executions').closest('.rounded-xl') as HTMLElement
    const value = card.querySelector('.tabular-nums') as HTMLElement
    expect(value.className.split(/\s+/)).toContain(`${PRESENTING}whitespace-nowrap`)
    const delta = card.querySelector('[title="Relative change vs the previous period of the same length"]') as HTMLElement
    expect(delta.textContent).toBe('▬ 0%')
    expect(delta.className.split(/\s+/)).toContain(`${PRESENTING}whitespace-nowrap`)
    expect((value.parentElement as HTMLElement).className.split(/\s+/)).toContain(`${PRESENTING}flex-wrap`)
    const grid = card.parentElement as HTMLElement
    expect(grid.className.split(/\s+/)).toEqual(expect.arrayContaining(['xl:grid-cols-6', `${PRESENTING}xl:grid-cols-4`]))
    // Desk mode is untouched: every new rule is scoped to the presentation attribute.
    for (const el of [value, value.parentElement as HTMLElement, grid]) {
      const unscoped = el.className.split(/\s+/).filter((c) => /whitespace-nowrap|flex-wrap|grid-cols-4/.test(c) && !c.startsWith(PRESENTING))
      expect(unscoped).toEqual([])
    }
  })

  it('the pass-rate trend REPLACES Execution trend (OD-4), beside the donut, above the two breakdowns', async () => {
    await renderPage()
    expect(await screen.findByRole('heading', { level: 3, name: 'Pass rate trend' }, { timeout: 10_000 })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Execution trend' })).toBeNull()
    const ids = Array.from(document.querySelectorAll('[data-catalogue-section]'), (el) => el.getAttribute('data-catalogue-section'))
    expect(ids).toEqual(['overview-trend', 'overview-donut', 'overview-top-failing', 'overview-categories'])
    // Blockers keeps its content and moves below the catalogue.
    const blockers = screen.getByRole('heading', { name: "What's blocking release" })
    const categories = document.querySelector('[data-catalogue-section="overview-categories"]') as HTMLElement
    expect(categories.compareDocumentPosition(blockers) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('the donut’s totals equal the KPI and the window’s status counts (OD-5)', async () => {
    await renderPage()
    const donut = (await screen.findByRole('heading', { level: 3, name: 'Status breakdown' }, { timeout: 10_000 })).closest('[data-chart-frame]') as HTMLElement
    fireEvent.click(within(donut).getByRole('button', { name: 'View as table' }))
    const table = within(donut).getByRole('table', { name: /data table/i })
    const count = (status: string) =>
      (within(table).getByRole('rowheader', { name: status }).parentElement as HTMLElement).querySelector('td')?.textContent
    // TREND summed: 85 passed, 7 failed, 8 broken, 8 skipped = 108, the KPI.
    expect({ Passed: count('Passed'), Failed: count('Failed'), Broken: count('Broken'), Skipped: count('Skipped') })
      .toEqual({ Passed: '85', Failed: '7', Broken: '8', Skipped: '8' })
    expect(donut.querySelector('[data-donut-table-total]')?.textContent).toBe('Total 108 executions')
    expect(kpiValue('Total executions')).toBe('108')
  })

  it('Failure categories draws the page’s own failure-categories read (one request, one SWR entry)', async () => {
    const { useFailureCategories } = await import('@/hooks/useMetrics')
    const mutate = vi.fn()
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ category: 'environment', count: 4 }, { category: 'assertion', count: 9 }] },
      error: undefined,
      isValidating: false,
      isLoading: false,
      mutate,
    })
    await renderPage()
    const frame = (await screen.findByRole('heading', { level: 3, name: 'Failure categories' }, { timeout: 10_000 })).closest(
      '[data-chart-frame]',
    ) as HTMLElement
    expect(frame.getAttribute('data-chart-state')).toBe('ready')
    fireEvent.click(within(frame).getByRole('button', { name: 'View as table' }))
    expect(within(frame).getByRole('rowheader', { name: 'environment' })).toBeInTheDocument()
    expect(within(frame).getByRole('rowheader', { name: 'assertion' })).toBeInTheDocument()
    // The page asked once, with its own window and scope; the section added no read.
    const windows = (useFailureCategories as ReturnType<typeof vi.fn>).mock.calls.map((call) => call[0])
    expect(new Set(windows)).toEqual(new Set([30]))
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined, isLoading: false })
  })
})
