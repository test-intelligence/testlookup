import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { FIRST_RUN_DISMISS_KEY, firstRunDismissKey } from '@/components/onboarding/firstRunSteps'

import OverviewPage from './OverviewPage'
import { KPI_STRIP_COLUMNS } from '@/components/ui/KpiStrip'

/** The router's current query string, for the `?tab=` tests. */
function LocationProbe() {
  const location = useLocation()
  return <span data-location={location.search} hidden />
}

// A disclosure remembers being opened (`persistKey`, localStorage): every test
// starts with "How this verdict is decided" closed.
beforeEach(() => {
  for (let i = localStorage.length - 1; i >= 0; i--) {
    const key = localStorage.key(i)
    if (key?.startsWith('tl.disclosure.')) localStorage.removeItem(key)
  }
})

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
// UX redesign P3: the Eng-hours saved KPI moved to Reports › Value, so Home
// no longer reads value metrics at all (and its Overview-only hook was
// deleted). The module's remaining hook is mocked as a spy: a re-introduced
// call would ask /value-metrics on every Home load again.
const useValueMetrics = vi.hoisted(() => vi.fn(() => ({ metrics: undefined })))
vi.mock('@/hooks/useValueMetrics', () => ({ useValueMetrics }))
// Recent activity (epic ACT) has its own tests; here it is a stand-in that
// says when it is mounted and with which window.
vi.mock('@/components/activity/RecentActivityPanel', () => ({
  default: ({ days }: { days: number }) => <div data-testid="recent-activity" data-days={days} />,
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

/**
 * The project HAS run, only not in the selected window. A zero-execution
 * window then shows the page (and its empty-window notice); a project that
 * never ran shows the getting-started guide alone (UX redesign P3).
 */
function pastRunOnly() {
  runsState.windowed = []
  runsState.newest = [{ id: 'r-old', created_at: new Date(Date.now() - 16 * 86_400_000).toISOString() }]
  runsState.everHad = undefined
}

/** A KPI tile of the strip (a compact `MetricCard`), by its title. */
function kpiCard(title: string): HTMLElement {
  return screen.getByText(title).closest('[data-metric-card]') as HTMLElement
}

/** A KPI tile's change line ("Up 400% vs prev period (worse)"). */
function kpiChange(title: string): HTMLElement {
  return kpiCard(title).querySelector('[data-metric-trend]') as HTMLElement
}

/** The one-line verdict banner (inside the "Release readiness" region). */
async function findBanner(): Promise<HTMLElement> {
  const region = await screen.findByRole('region', { name: 'Release readiness' })
  return region.querySelector('[data-status-banner]') as HTMLElement
}

/** Open "How this verdict is decided" (collapsed below the charts) and return its content. */
async function openVerdictDetails(): Promise<HTMLElement> {
  fireEvent.click(await screen.findByRole('button', { name: /^How this verdict is decided/ }))
  return screen.getByTestId('verdict-details')
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
    analyticsViewState.widgetIds = []
    useValueMetrics.mockClear()
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

    // The 400 must carry its unit; without it this reads as 400 executions.
    // Scoped to the tile's change line: a page-wide match would pass on any
    // other surface that happened to print a percentage.
    await screen.findByText('Total executions')
    expect(kpiChange('Total executions')).toHaveTextContent(/^Up 400% vs prev period/)
    expect(kpiChange('Total executions').textContent).not.toMatch(/\b400\b(?!%)/)
    // Downward trends too — the direction is a word, the size a percentage.
    expect(kpiChange('Avg pass rate')).toHaveTextContent(/^Down 2\.2% vs prev period/)
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
    // An unchanged metric says so in words — never a bare "0" beside a count,
    // which reads as "zero runs", not "no change".
    expect(kpiChange('Total executions')).toHaveTextContent(/^No change vs prev period$/)
    expect(kpiChange('Total executions').textContent).not.toMatch(/\b0\b/)
    // A metric the API sent no direction for has no change line at all: no
    // "no change" nobody measured.
    expect(kpiChange('Avg pass rate')).toBeNull()
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

    // The change line says what it is compared with, and its full text
    // (with the judgement) is its title when a narrow tile truncates it.
    await screen.findByText('Total executions')
    expect(kpiChange('Total executions')).toHaveTextContent(/vs prev period/)
    expect(kpiChange('Total executions')).toHaveAttribute('title', expect.stringMatching(/vs prev period/i))
  })

  // UX redesign P3: "Eng-hours saved" leaves Home for Reports › Value (spec
  // §5, Home, Delete). Home neither shows it nor asks for it.
  it('shows no Eng-hours saved KPI and does not read value metrics (it lives on Reports › Value)', async () => {
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await findBanner()
    expect(screen.queryByText('Eng-hours saved')).toBeNull()
    expect(screen.queryByText(/View value metrics/i)).toBeNull()
    expect(useValueMetrics).not.toHaveBeenCalled()
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

    const banner = await findBanner()
    expect(screen.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument()
    // The ribbon's four invented stages (and the heading that named it) are
    // gone, and the verdict no longer shares a row with anything.
    expect(screen.queryByText(/Quality workflow/i)).toBeNull()
    for (const stage of ['Quality Snapshot', 'Readiness Check', 'Trend Analysis', 'Action Focus']) {
      expect(screen.queryByRole('link', { name: `Open ${stage}` }), stage).toBeNull()
    }
    // UX redesign P3: the verdict is ONE line — ``GREEN`` readiness is the GO
    // pill and its words — with the way to the failures.
    expect(banner).toHaveAttribute('data-status-banner', 'go')
    expect(within(banner).getByText('GO')).toBeInTheDocument()
    expect(banner).toHaveTextContent('ship cleared')
    expect(within(banner).getByRole('link', { name: /Open failures/ })).toHaveAttribute('href', '/failures')
    // What the verdict IS generated from (the readiness band) and the window it
    // covers — not "generated just now" (the render time) — sit in the
    // detail under the charts.
    const details = await openVerdictDetails()
    expect(within(details).getByText(/release-readiness band/)).toBeInTheDocument()
    expect(details).toHaveTextContent(/Verdict for the last \d+ days, from the release-readiness band/)
    expect(details.textContent).not.toMatch(/just now/)
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

    expect(await findBanner()).toBeInTheDocument()
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
    pastRunOnly()

    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )

    // Zero-executions case must surface as the PENDING verdict (via
    // ``mapReadinessToVerdict``), not the RED the backend sent, nor the
    // misleading "Critical issues must be resolved" copy.
    const banner = await findBanner()
    expect(banner).toHaveAttribute('data-status-banner', 'pending')
    expect(within(banner).getByText('PENDING')).toBeInTheDocument()
    expect(banner).toHaveTextContent('awaiting evidence')
    expect(within(banner).queryByText('NO-GO')).toBeNull()
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

    const banner = await findBanner()
    // Fabricated ribbon literals must be gone (the dashboard has no real
    // per-run cost / evidence-count / stage-duration signal to report).
    expect(screen.queryByText(/\$0\.31/)).toBeNull()
    expect(screen.queryByText(/evidence items/i)).toBeNull()
    // The invented "readiness confidence %" is replaced by the real pass rate.
    expect(screen.queryByText(/Readiness confidence/i)).toBeNull()
    expect(banner).toHaveTextContent(/Pass rate 92%/)
    // Dead CTAs (no-op handlers) are removed, not shipped as inert buttons.
    expect(screen.queryByRole('button', { name: /Run quality workflow/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /View evidence/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /Override gate/i })).toBeNull()
  })

  // ── P2: remove the noise; P3: five KPIs (spec §5, Home) ─────────────────
  const ALL_KPIS = ['Total executions', 'Avg pass rate', 'New failures · 24h', 'Flaky tests', 'Active defects']

  it('renders the five KPI tiles whatever the saved widget selection, and offers no Customize (P2, P3)', async () => {
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
      await findBanner()
      const strip = document.querySelector('[data-kpi-strip]') as HTMLElement
      expect(Array.from(strip.querySelectorAll('[data-metric-card="compact"] > p:first-child'), (p) => p.textContent)).toEqual(ALL_KPIS)
      // P3: the two tiles that left the strip, and the one that moved to Reports › Value.
      for (const gone of ['Infra-caused failures', 'Avg run duration', 'Eng-hours saved']) {
        expect(screen.queryByText(gone), gone).toBeNull()
      }
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
    await findBanner()
    // The verdict's three reason cards (each a KPI card again).
    expect(screen.queryByText('Sample size')).toBeNull()
    // The coverage micro-strip: executions, avg duration (both KPI cards), an
    // inferred "last green run", and a Mean time to fix that was always "—".
    for (const gone of [/Automation coverage/i, /Last green run/i, /Mean time to fix/i]) {
      expect(screen.queryByText(gone), String(gone)).toBeNull()
    }
    expect(screen.queryByText(/needs ≥ 3 fixes/)).toBeNull()
    // Each KPI tile once — no second block repeating it. (The banner's "New
    // failures · 24h" fact is the verdict's, per spec §5.)
    const titles = Array.from(document.querySelectorAll('[data-metric-card] > p:first-child'), (p) => p.textContent)
    for (const label of ALL_KPIS) expect(titles.filter((t) => t === label), label).toHaveLength(1)
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
    pastRunOnly()
    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes><Route path="/overview" element={<OverviewPage />} /></Routes>
      </MemoryRouter>,
    )
    // The banner already says it (PENDING, a sample of 0), and so do the
    // verdict's detail and the empty-window notice.
    const banner = await findBanner()
    expect(banner).toHaveTextContent(/Sample 0 executions · 30d/)
    expect(await openVerdictDetails()).toHaveTextContent(/No test executions in the last \d+ days/)
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
   * The verdict's statements of its sample, as one string: the banner's
   * "Sample" fact and the verdict detail's sentence. P2 removed the "Sample
   * size" reason card (the Total executions KPI is the same number); P3 put
   * the verdict on one line, with its reasoning under the charts.
   */
  async function verdictSampleText(): Promise<string> {
    const banner = await findBanner()
    const fact = Array.from(banner.querySelectorAll('[data-banner-fact]')).find((el) => el.textContent?.startsWith('Sample'))
    const details = await openVerdictDetails()
    const sentence = within(details).getByText(/quality gates passed/).textContent ?? ''
    return `${fact?.textContent ?? ''} | ${sentence}`
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
    expect(kpiChange('Total executions')).toHaveTextContent(/^Up 680% vs prev period/)
  })
})

// UX redesign P3: the "What's blocking release" panel is folded into the
// verdict banner (its 24-hour count is the banner's "New failures · 24h", its
// words the banner's, its link the banner's "Open failures"). The guards on
// the WORDS stay: they now hold for the banner and the verdict detail.
describe('OverviewPage — the 24-hour new failures describe their own metric', () => {
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
    await findBanner()
    await openVerdictDetails()
    expect(
      screen.queryByText(/new failures? in the window/i),
      'the panel says "in the window" for a value that ignores the window selector',
    ).toBeNull()
  })

  it('does not attribute the count to "the last green run"', async () => {
    await renderWithFailures()
    await findBanner()
    await openVerdictDetails()
    expect(
      screen.queryByText(/since the last green run/i),
      'the panel attributes a fixed 24h count to a green-run baseline that is ' +
        'not part of the computation',
    ).toBeNull()
  })

  it('still states the 24h window it actually measures', async () => {
    await renderWithFailures()
    const banner = await findBanner()
    expect(banner).toHaveTextContent(/New failures · 24h 3/)
    // The panel itself is gone, and its link is the banner's.
    expect(screen.queryByText(/What's blocking release/i)).toBeNull()
    expect(within(banner).getByRole('link', { name: /Open failures/ })).toHaveAttribute('href', '/failures')
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

    const banner = await findBanner()
    expect(banner).toHaveAttribute('data-status-banner', 'no_go')
    expect(banner).toHaveTextContent('ship blocked by unresolved failures')
    expect(banner).toHaveTextContent(/New failures · 24h 0/)
    expect(screen.queryByText('Nothing is blocking release.')).toBeNull()
    expect(await openVerdictDetails()).toHaveTextContent(/No new failures in the last 24 h; existing failures still require resolution/i)
  })
})

describe('OverviewPage — a KPI caption must not deny its own value', () => {
  // UX redesign P3: the caption sits where the tile's trend line would, beside
  // the value (a compact MetricCard), and still explains the missing LINE.
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
    pastRunOnly()
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
    // With the getting-started guide dismissed: undismissed, an empty project
    // shows the guide alone (P3), and the guide says this itself.
    localStorage.setItem(firstRunDismissKey('proj-1'), '1')
    try {
      renderPage()

      const banner = await screen.findByTestId('overview-empty-window')
      expect(banner.textContent).toMatch(/No test runs yet/i)
      expect(banner.textContent).toMatch(/widening the time window will not help/i)
      expect(banner.textContent).not.toMatch(/Show last/i)
    } finally {
      localStorage.removeItem(firstRunDismissKey('proj-1'))
    }
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
    // UX redesign P3 (spec §5, Home): an empty project shows ONLY the guide,
    // under the title — no verdict, no five zero tiles, no empty charts.
    expect(screen.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Release readiness' })).toBeNull()
    expect(document.querySelector('[data-kpi-strip]')).toBeNull()
    expect(document.querySelector('[data-primary]')).toBeNull()
    expect(screen.queryByTestId('overview-empty-window')).toBeNull()
    expect(document.querySelector('[data-disclosure]')).toBeNull()
    expect(screen.queryByRole('tablist', { name: 'Dashboard sections' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /dismiss getting started/i }))

    // Gone immediately (reactive to the dismiss, no reload needed), and the
    // page under it says why it is empty…
    expect(screen.queryByText(/Welcome to TestLookup/i)).toBeNull()
    expect(screen.getByTestId('overview-empty-window')).toHaveTextContent(/No test runs yet/)
    expect(document.querySelector('[data-kpi-strip]')).not.toBeNull()
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

  it('never hides executions the summary counted behind the guide', async () => {
    // The guide replaces the page, so a summary with executions in it keeps
    // the page even when the run list (a different read) came back empty.
    runsState.everHad = undefined
    const { useDashboardSummary, useTrendData } = await import('@/hooks/useMetrics')
    mockDashboardData(useDashboardSummary, useTrendData)   // 120 executions
    runsState.windowed = []
    runsState.newest = []
    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )
    await findBanner()
    expect(screen.queryByText(/Welcome to TestLookup/i)).toBeNull()
    expect(kpiCard('Total executions')).toHaveTextContent('120')
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
    // The meter is the verdict's detail (P3): collapsed under the charts.
    expect(screen.queryByRole('meter', { name: 'Pass rate' })).toBeNull()
    await openVerdictDetails()
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
    pastRunOnly()
    render(
      <MemoryRouter initialEntries={['/overview']}>
        <Routes>
          <Route path="/overview" element={<OverviewPage />} />
        </Routes>
      </MemoryRouter>,
    )
    expect(await findBanner()).toHaveAttribute('data-status-banner', 'pending')
    await openVerdictDetails()
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
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-08-16T12:00:00Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  /** The page at `entry` (default `/overview`); `[data-location]` shows the current search. */
  async function renderPage(entry = '/overview') {
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
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route
            path="/overview"
            element={
              <>
                <OverviewPage />
                <LocationProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>,
    )
  }

  const sectionIds = () =>
    Array.from(document.querySelectorAll('[data-catalogue-section]'), (el) => el.getAttribute('data-catalogue-section'))
  const search = () => document.querySelector('[data-location]')?.getAttribute('data-location')

  const kpiValue = (label: string) => kpiCard(label).querySelector('p.tabular-nums')?.textContent

  // UX redesign P3: the KPI tiles are the shared compact `MetricCard` in one
  // `KpiStrip`. A value never breaks across lines (`whitespace-nowrap`, R2-13)
  // and the change line truncates to one line with its full text as a title.
  it('draws the five KPIs as compact cards in one strip, values and changes each on one line', async () => {
    await renderPage()
    const strip = document.querySelector('[data-kpi-strip]') as HTMLElement
    // The strip's own width-fitting columns (one row of five on a desktop page).
    expect(strip.style.gridTemplateColumns).toBe(KPI_STRIP_COLUMNS)
    expect(strip.querySelectorAll('[data-metric-card="compact"]')).toHaveLength(5)
    const values = Array.from(strip.querySelectorAll('[data-metric-card="compact"] p.tabular-nums'))
    expect(values.map((v) => v.textContent)).toEqual(['108', '85%', '0', '0', '0'])
    for (const v of values) expect(v.className.split(/\s+/)).toContain('whitespace-nowrap')
  })

  it('opens the page behind a KPI from its tile', async () => {
    await renderPage()
    const destinations = Array.from(document.querySelectorAll('[data-kpi-strip] a'), (a) => [
      a.querySelector('[data-metric-card] > p')?.textContent,
      a.getAttribute('href'),
    ])
    expect(destinations).toEqual([
      ['Total executions', '/runs'],
      ['New failures · 24h', '/failures'],
      ['Flaky tests', '/flaky'],
      ['Active defects', '/defects'],
    ])
  })

  it('the pass-rate trend REPLACES Execution trend (OD-4), beside the donut, above the two breakdowns', async () => {
    await renderPage()
    expect(await screen.findByRole('heading', { level: 3, name: 'Pass rate trend' }, { timeout: 10_000 })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Execution trend' })).toBeNull()
    // The headline row (primary) then the breakdown row (the default tab): the same four, in the same order.
    await screen.findByRole('heading', { level: 3, name: 'Top failing tests' }, { timeout: 10_000 })
    expect(sectionIds()).toEqual(['overview-trend', 'overview-donut', 'overview-top-failing', 'overview-categories'])
    // P3: the "What's blocking release" panel is folded into the verdict banner.
    expect(screen.queryByRole('heading', { name: "What's blocking release" })).toBeNull()
  })

  // ── UX redesign P3: the page template ───────────────────────────────────
  it('puts the primary content (trend + donut) right under the banner and the strip, before the tab bar and the disclosure', async () => {
    await renderPage()
    await screen.findByRole('heading', { level: 3, name: 'Pass rate trend' }, { timeout: 10_000 })
    const primary = document.querySelector('[data-primary]') as HTMLElement
    expect(document.querySelectorAll('[data-primary]')).toHaveLength(1)
    // The primary is the headline row ONLY: the breakdown row is the Top failing tab's.
    expect(Array.from(primary.querySelectorAll('[data-catalogue-section]'), (el) => el.getAttribute('data-catalogue-section'))).toEqual([
      'overview-trend',
      'overview-donut',
    ])
    // Above it, in order: the header, the banner, the strip.
    const header = document.querySelector('[data-page-header]') as HTMLElement
    const banner = document.querySelector('[data-status-banner]') as HTMLElement
    const strip = document.querySelector('[data-kpi-strip]') as HTMLElement
    const follows = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
    expect(follows(header, banner) && follows(banner, strip) && follows(strip, primary)).toBe(true)
    // Below it: the tab bar (Top failing · Activity), then the one disclosure.
    const secondary = Array.from(document.querySelectorAll('[role="tablist"], [data-disclosure]'))
    expect(secondary.map((el) => el.getAttribute('aria-label') ?? el.querySelector('button')?.textContent?.replace(/pass rate.*$/, ''))).toEqual([
      'Dashboard sections',
      'How this verdict is decided',
    ])
    for (const el of secondary) expect(follows(primary, el)).toBe(true)
    // The page's block gap is the template's.
    expect((primary.parentElement as HTMLElement).className.split(/\s+/)).toContain('space-y-4')
  })

  it('opens on Top failing (the clean URL): the breakdown row in its panel, no activity feed', async () => {
    await renderPage()
    const tablist = screen.getByRole('tablist', { name: 'Dashboard sections' })
    expect(within(tablist).getAllByRole('tab').map((t) => t.textContent)).toEqual(['Top failing', 'Activity'])
    expect(within(tablist).getByRole('tab', { name: 'Top failing' })).toHaveAttribute('aria-selected', 'true')
    const panel = screen.getByRole('tabpanel', { name: 'Top failing' })
    await within(panel).findByRole('heading', { level: 3, name: 'Top failing tests' }, { timeout: 10_000 })
    expect(Array.from(panel.querySelectorAll('[data-catalogue-section]'), (el) => el.getAttribute('data-catalogue-section'))).toEqual([
      'overview-top-failing',
      'overview-categories',
    ])
    expect(screen.queryByTestId('recent-activity')).toBeNull()
    expect(search()).toBe('')
  })

  it('?tab=activity opens Activity: the feed with the page’s window, and no breakdown row', async () => {
    await renderPage('/overview?tab=activity')
    expect(screen.getByRole('tab', { name: 'Activity' })).toHaveAttribute('aria-selected', 'true')
    const panel = screen.getByRole('tabpanel', { name: 'Activity' })
    expect(within(panel).getByTestId('recent-activity')).toHaveAttribute('data-days', '30')
    await screen.findByRole('heading', { level: 3, name: 'Pass rate trend' }, { timeout: 10_000 })
    expect(sectionIds()).toEqual(['overview-trend', 'overview-donut'])
  })

  it('an unknown ?tab= reads as Top failing', async () => {
    await renderPage('/overview?tab=nope')
    expect(screen.getByRole('tab', { name: 'Top failing' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByTestId('recent-activity')).toBeNull()
  })

  it('mounts Recent activity only once its tab is opened, writes ?tab=activity, and the default tab clears it', async () => {
    await renderPage()
    expect(screen.queryByTestId('recent-activity')).toBeNull()
    fireEvent.click(screen.getByRole('tab', { name: 'Activity' }))
    expect(screen.getByTestId('recent-activity')).toHaveAttribute('data-days', '30')
    expect(search()).toBe('?tab=activity')
    // Leaving the tab unmounts the feed and brings the breakdown row back.
    fireEvent.click(screen.getByRole('tab', { name: 'Top failing' }))
    expect(screen.queryByTestId('recent-activity')).toBeNull()
    expect(search()).toBe('')
    await screen.findByRole('heading', { level: 3, name: 'Top failing tests' }, { timeout: 10_000 })
  })

  it('keeps the window in the header: the shared WindowPicker, bound to the global window', async () => {
    const { useTimeWindowStore } = await import('@/store/timeWindowStore')
    await renderPage()
    const header = document.querySelector('[data-page-header]') as HTMLElement
    const picker = within(header).getByRole('radiogroup', { name: 'Time window' })
    expect(within(picker).getAllByRole('radio').map((r) => r.textContent)).toEqual(['24h', '7d', '14d', '30d', '90d'])
    expect(within(picker).getByRole('radio', { name: '30d' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(within(picker).getByRole('radio', { name: '7d' }))
    expect(useTimeWindowStore.getState().days).toBe(7)
    const { useDashboardSummary } = await import('@/hooks/useMetrics')
    const summaryCalls = (useDashboardSummary as ReturnType<typeof vi.fn>).mock.calls
    expect(summaryCalls[summaryCalls.length - 1]?.[0]).toBe(7)
    useTimeWindowStore.getState().setDays(30)
    // The suite filter sits beside it, and the help topic is the dashboards page.
    expect(within(header).getByTitle('Filter dashboard metrics by test suite')).toBeInTheDocument()
    expect(within(header).getByRole('button', { name: 'Help: Dashboard' })).toHaveAttribute('data-help-topic', 'dashboards')
  })

  it('opens and closes "How this verdict is decided" below the charts', async () => {
    await renderPage()
    expect(screen.queryByTestId('verdict-details')).toBeNull()
    const button = screen.getByRole('button', { name: /^How this verdict is decided/ })
    // Its summary carries the pass rate and the population it is over.
    expect(button).toHaveTextContent('pass rate 85% · weighted · 30d')
    fireEvent.click(button)
    expect(screen.getByTestId('verdict-details')).toHaveTextContent(/All quality gates passed across 108 test executions/)
    expect(screen.getByTestId('verdict-pass-rate-basis')).toHaveTextContent('85% · weighted · 30d')
    fireEvent.click(button)
    expect(screen.queryByTestId('verdict-details')).toBeNull()
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
    // Only this render's calls (an earlier test picked another window).
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockClear()
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
