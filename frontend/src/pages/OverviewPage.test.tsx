import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { FIRST_RUN_DISMISS_KEY, firstRunDismissKey } from '@/components/onboarding/firstRunSteps'

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

const runsState: { windowed: unknown[]; newest: unknown[] | null } = { windowed: [], newest: [] }
vi.mock('@/hooks/useRuns', () => ({
  // Two calls with different params: the windowed list and the single newest
  // run. Distinguished by `size`, exactly as the page does.
  useRuns: vi.fn((params?: { size?: number }) => {
    const items = params?.size === 1 ? runsState.newest : runsState.windowed
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
    analyticsViewState.widgetIds = [
      'total_executions_kpi', 'avg_pass_rate_kpi', 'new_failures_kpi',
    ]
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
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        data: [
          { date: '2026-08-16', passed: 31, failed: 17, skipped: 6, broken: 6, total: 60, pass_rate: 57.4 },
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
    // And the execution-trend chart blamed runs for the same shortfall.
    expect(screen.queryByText(/2 timed runs/)).not.toBeInTheDocument()
    expect(screen.getByText(/1 of 30 days has data — a trend line needs at least 2/)).toBeInTheDocument()
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

  async function renderEmptyProject(newest: unknown[] | null = []) {
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
