import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type StackedColumnChartFrame from '@/components/charts/StackedColumnChartFrame'
import type TimeSeriesChartFrame from '@/components/charts/TimeSeriesChartFrame'
import { utcDayLabel } from '@/components/charts/stackedColumnModel'
import { chartConfigStore, useChartConfigStore } from '@/store/chartConfigStore'
import { NOT_AMONG_BUSIEST } from '@/components/reports/catalogue/trendsCatalogueParams'
import TrendsPage from './TrendsPage'

// The two kit frames render for real; these spies only record what the page
// handed them, so a test can read the page's adapter output (statuses, gaps,
// the target) rather than infer it from a zero-size jsdom plot.
const frames = vi.hoisted(() => ({
  stacked: [] as unknown[],
  timeSeries: [] as unknown[],
}))

// The section-error test (R1-1) makes a boundary report an error; the real
// reporter batches it for 5 s and then POSTs it, which under load lands inside
// a LATER test and trips the network guard there (W08 gate, coverage run).
// Same guard as ReleaseGatePage.test.tsx: keep the module, stub the report.
vi.mock('@/utils/errorReporting', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/utils/errorReporting')>(),
  reportBoundaryError: vi.fn(),
}))

// P1: the header's Views menu reads this page's saved views (none here).
vi.mock('@/services/savedViewsService', () => ({
  listSavedViews: vi.fn(async () => []),
  createSavedView: vi.fn(),
  updateSavedView: vi.fn(),
  deleteSavedView: vi.fn(),
}))
vi.mock('@/components/charts/StackedColumnChartFrame', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/components/charts/StackedColumnChartFrame')>()
  const Real = actual.default
  return {
    ...actual,
    default: (props: ComponentProps<typeof StackedColumnChartFrame>) => {
      frames.stacked.push(props)
      return <Real {...props} />
    },
  }
})

vi.mock('@/components/charts/TimeSeriesChartFrame', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/components/charts/TimeSeriesChartFrame')>()
  const Real = actual.default
  function SpiedTimeSeriesChartFrame(props: ComponentProps<typeof TimeSeriesChartFrame>) {
    frames.timeSeries.push(props)
    return <Real {...props} />
  }
  return { ...actual, default: SpiedTimeSeriesChartFrame }
})

// Wave 2.6 (VIZ-408): the release list the pass-rate markers read, and the
// lazy catalogue chunk, each controlled here. The catalogue is a stub that
// records its props: its own sections are TrendsCatalogue.test.tsx's. Since
// Phase D (S3) the page asks no flag: both always render.

const releaseList = vi.hoisted(() => ({ items: [] as { id: string; name: string; released_at: string | null; planned_date: string | null }[] }))
vi.mock('@/hooks/useReleases', () => ({
  useReleases: () => ({ data: { items: releaseList.items } }),
}))

// `throws`: the section's top level throws (or its chunk failed to load).
const catalogue = vi.hoisted(() => ({
  props: [] as { days: number; suiteFilter: unknown; sections?: readonly string[] }[],
  throws: false,
}))
vi.mock('@/components/reports/catalogue/TrendsCatalogue', () => ({
  default: (props: { days: number; suiteFilter: unknown; sections?: readonly string[] }) => {
    catalogue.props.push(props)
    if (catalogue.throws) throw new Error('Failed to fetch dynamically imported module')
    return <div data-testid="trends-catalogue" />
  },
}))

vi.mock('@/hooks/useMetrics', () => ({
  useTrendData: vi.fn(),
  useDashboardSummary: vi.fn(),
  useCoverage: vi.fn(),
}))

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
}))

// One suite to pick, so a test can apply a filter; nothing else reads it.
vi.mock('@/hooks/useSuiteOptions', () => ({
  useSuiteOptions: vi.fn(() => ({ options: ['Checkout'], isLoading: false })),
}))

// P2: the page no longer reads a widget selection. The hook stays mocked with
// a saved EMPTY selection (a user who once unticked everything): it must not
// hide a section.
const analyticsControls = vi.hoisted(() => ({
  widgetIds: [] as string[],
}))

vi.mock('@/hooks/useAnalyticsView', () => ({
  useAnalyticsView: () => ({
    widgetIds: analyticsControls.widgetIds,
    setWidgets: vi.fn(),
    save: vi.fn(),
  }),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string; activeProject: { name: string } | null }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { name: 'Project One' } })),
}))

// ── W08 ────────────────────────────────────────────────────────────────────
// TRND-06: the verdict, the score and the gap are the SERVER's trend summary.
// The mock computes it (contract §1.3) from whatever trend and suites the test
// handed the page's two reads, so every older test still sees the verdict its
// trend implies; `override` hands the page a summary (or garbage) of its own.
const trendSummary = vi.hoisted(() => ({ override: undefined as unknown, error: null as unknown, calls: [] as unknown[][] }))
vi.mock('@/hooks/useTrendSummary', async () => {
  const metrics = await import('@/hooks/useMetrics')
  const { summaryFromTrend } = await import('@/test/trendSummaryFixture')
  const { parseTrendSummary } = await import('@/types/trends')
  const lastValue = (hook: unknown) => {
    const results = (hook as ReturnType<typeof vi.fn>).mock.results
    return results.length ? (results[results.length - 1].value as { data?: { data?: unknown[]; suites?: unknown[] } } | undefined) : undefined
  }
  return {
    useTrendSummary: (days: number, suiteFilter: unknown) => {
      trendSummary.calls.push([days, suiteFilter])
      if (trendSummary.override !== undefined) {
        return { summary: parseTrendSummary(trendSummary.override), isLoading: false, error: trendSummary.error }
      }
      const trend = (lastValue(metrics.useTrendData)?.data?.data ?? []) as Parameters<typeof summaryFromTrend>[0]
      const suites = (lastValue(metrics.useCoverage)?.data?.suites ?? []) as Parameters<typeof summaryFromTrend>[2]
      return { summary: summaryFromTrend(trend, days, suites), isLoading: false, error: null }
    },
  }
})

// TRND-06: the Suite pass rates sparklines (the hook's own request is its test's).
const sparklines = vi.hoisted(() => ({
  lines: {} as Record<string, (number | null)[]>,
  state: 'ready' as 'idle' | 'loading' | 'ready' | 'unavailable',
  calls: [] as unknown[][],
}))
vi.mock('@/hooks/useSuiteSparklines', () => ({
  useSuiteSparklines: (days: number, suiteFilter: unknown, near: boolean) => {
    sparklines.calls.push([days, suiteFilter, near])
    return { lineOf: (name: string) => sparklines.lines[name.trim().toLowerCase()], state: sparklines.state }
  },
}))

// TRND-04: the window's timeline notes and their writes.
const timeline = vi.hoisted(() => ({ notes: [] as unknown, truncated: false, calls: [] as unknown[][] }))
vi.mock('@/hooks/useTimelineNotes', () => ({
  useTimelineNotes: (range: unknown, suite: unknown) => {
    timeline.calls.push([range, suite])
    return { notes: timeline.notes, total: 0, truncated: timeline.truncated, isLoading: false, unavailable: false, mutate: async () => undefined }
  },
  useTimelineNoteActions: () => ({ projectId: 'proj-1', create: vi.fn(), update: vi.fn(), remove: vi.fn() }),
}))

// Who is reading: a viewer unless a test says otherwise.
const role = vi.hoisted(() => ({ isQaLead: false, isQaEngineer: false, isAdmin: false }))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ role: 'VIEWER', ...role, hasRole: () => false }),
}))

// TRND-01: the PDF download (the service and the browser download, mocked).
const reportService = vi.hoisted(() => ({ downloadTrendsPdf: vi.fn() }))
vi.mock('@/services/trendsReportService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/trendsReportService')>()),
  downloadTrendsPdf: reportService.downloadTrendsPdf,
}))
const download = vi.hoisted(() => ({ downloadBlob: vi.fn() }))
vi.mock('@/utils/download', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/utils/download')>()),
  downloadBlob: download.downloadBlob,
}))

// The two dialogs are their own tests'; here they record what the page opened them with.
const dialogs = vi.hoisted(() => ({ pin: [] as Record<string, unknown>[], email: [] as Record<string, unknown>[] }))
vi.mock('@/components/trends/PinNoteDialog', () => ({
  default: (props: Record<string, unknown>) => {
    dialogs.pin.push(props)
    return <div data-testid="pin-note-dialog" />
  },
}))
vi.mock('@/components/trends/EmailReportDialog', () => ({
  default: (props: Record<string, unknown>) => {
    dialogs.email.push(props)
    return <div data-testid="email-report-dialog" />
  },
}))

// Every W08 control starts from a viewer, no notes, the computed summary and a clean chart store.
beforeEach(() => {
  trendSummary.override = undefined
  trendSummary.error = null
  trendSummary.calls = []
  sparklines.lines = {}
  sparklines.state = 'ready'
  sparklines.calls = []
  timeline.notes = []
  timeline.truncated = false
  timeline.calls = []
  role.isQaLead = false
  role.isQaEngineer = false
  role.isAdmin = false
  reportService.downloadTrendsPdf.mockReset()
  download.downloadBlob.mockReset()
  dialogs.pin.length = 0
  dialogs.email.length = 0
  useChartConfigStore.setState({ applied: {}, viewToken: 0 })
  window.localStorage.removeItem('testlookup.chartConfig.trends.compare')
  window.localStorage.removeItem('testlookup.chartConfig.trends.suite-series')
})

type StackedProps = ComponentProps<typeof StackedColumnChartFrame>
type TimeSeriesProps = ComponentProps<typeof TimeSeriesChartFrame>

/** The last props the page gave the daily-breakdown frame, with its model non-null. */
function lastStacked(): StackedProps & { model: NonNullable<StackedProps['model']> } {
  const props = frames.stacked[frames.stacked.length - 1] as StackedProps | undefined
  if (!props?.model) throw new Error('the daily breakdown frame was not rendered with a model')
  return props as StackedProps & { model: NonNullable<StackedProps['model']> }
}

function lastTimeSeries(): TimeSeriesProps & { model: NonNullable<TimeSeriesProps['model']> } {
  const props = frames.timeSeries[frames.timeSeries.length - 1] as TimeSeriesProps | undefined
  if (!props?.model) throw new Error('the pass-rate frame was not rendered with a model')
  return props as TimeSeriesProps & { model: NonNullable<TimeSeriesProps['model']> }
}

/** P3: the verdict, its score and its dimensions are in a collapsed disclosure below the hero. */
function openScore() {
  fireEvent.click(screen.getByRole('button', { name: /How this score is computed/ }))
}

/** P3: the page's own tabs (Volume is the default). */
function openTab(name: 'Volume' | 'By suite' | 'Execution time' | 'Heatmap') {
  fireEvent.click(within(screen.getByRole('tablist', { name: 'Trend views' })).getByRole('tab', { name }))
}

/** The shared window picker's option (`WindowPicker`: a radio group). */
const windowOption = (label: string) => within(screen.getByRole('radiogroup', { name: 'Time window' })).getByRole('radio', { name: label })

/** The run-cadence strip's sr-only table, one [day, state, marker] per cell. */
function cadenceRows(): string[][] {
  const table = screen.getByRole('table', { name: /^Run cadence:/ })
  return [...(table as HTMLTableElement).tBodies[0].rows].map((row) => [...row.cells].map((cell) => cell.textContent ?? ''))
}

describe('TrendsPage', () => {
  beforeEach(() => {
    analyticsControls.widgetIds = []
    frames.stacked.length = 0
    frames.timeSeries.length = 0
  })

  // P2: the 3-stage "Trends workflow" ribbon was invented (fixed stages, fixed
  // evidence counts, a hard-coded 91% confidence) and is gone.
  it('renders the verdict and the charts, and no invented workflow ribbon', async () => {
    const { useTrendData, useDashboardSummary, useCoverage } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        data: [
          { date: '2026-04-01', passed: 16, failed: 2, skipped: 1, broken: 0, pass_rate: 88 },
          { date: '2026-04-02', passed: 18, failed: 1, skipped: 0, broken: 0, pass_rate: 95 },
        ],
      },
      isLoading: false,
    })
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { avg_pass_rate_7d: { value: 91, trend: 2 } },
    })
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        summary: { unique_tests: 2, suite_count: 1, total_executions: 38, avg_pass_rate: 91, days_with_runs: 2 },
        suites: [{ suite_name: 'Checkout', unique_tests: 2, passed: 34, failed: 3, skipped: 1, pass_rate: 91 }],
      },
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })

    render(
      <MemoryRouter initialEntries={['/trends']}>
        <Routes>
          <Route path="/trends" element={<TrendsPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Trends' })).toBeInTheDocument()
    // P3: the verdict is in the score disclosure, collapsed until asked for.
    expect(screen.queryByRole('region', { name: 'Trend verdict' })).toBeNull()
    openScore()
    expect(screen.getByRole('region', { name: 'Trend verdict' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Daily breakdown' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Trends workflow' })).toBeNull()
    expect(screen.queryByText(/Trends workflow/i)).toBeNull()
    expect(screen.queryByText(/Trend capture|Signal comparison|Report delivery/)).toBeNull()
    expect(screen.queryByText(/91% confidence/)).toBeNull()
    expect(screen.queryByText(/evidence items/)).toBeNull()
  })

  // P2: the widget picker is gone, so a selection saved with it (here: none
  // ticked) must not keep hiding a section forever.
  it('renders every section whatever widget selection was saved', async () => {
    const { useTrendData, useDashboardSummary, useCoverage } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')
    analyticsControls.widgetIds = []
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [{ date: '2026-04-01', passed: 4, failed: 1, skipped: 0, broken: 0, pass_rate: 80 }] },
      isLoading: false,
    })
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({ data: {} })
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({ data: { summary: {}, suites: [] } })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })

    render(
      <MemoryRouter initialEntries={['/trends']}>
        <Routes><Route path="/trends" element={<TrendsPage />} /></Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Trends' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Trend metrics' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Daily breakdown' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Pass rate trend' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Customize/ })).toBeNull()
  })

  // Regression: the headline pass rate used to divide by every execution,
  // skips included — 31/60 = 51.7% — while the same payload's `pass_rate`
  // field and /overview both said 57.4% (31 evaluated-of-54, skips excluded).
  // Two figures for one window on two pages. The denominator is now the
  // backend's: passed + failed + broken.
  it('excludes skips from the headline pass rate, matching the API and /overview', async () => {
    const { useTrendData, useDashboardSummary, useCoverage } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')

    // The ground-truth fixture: one day, 60 executions, 6 of them skipped.
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        data: [
          { date: '2026-08-16', passed: 31, failed: 17, skipped: 6, broken: 6, total: 60, pass_rate: 57.4 },
        ],
      },
      isLoading: false,
    })
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({ data: {} })
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        summary: { unique_tests: 10, suite_count: 1, total_executions: 60, avg_pass_rate: 57.4, days_with_runs: 1 },
        // `failed` folds broken in, so passed + failed is the evaluated count.
        suites: [{ suite_name: 'GroundTruthSuite', unique_tests: 10, passed: 31, failed: 23, skipped: 6, pass_rate: 57.4 }],
      },
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })

    render(
      <MemoryRouter initialEntries={['/trends']}>
        <Routes>
          <Route path="/trends" element={<TrendsPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect((await screen.findAllByText('57.4%')).length).toBeGreaterThan(0)
    // The pre-fix figure must not appear anywhere on the page.
    expect(screen.queryAllByText('51.7%')).toHaveLength(0)
    // The narrative rounds, so the old 51.7% surfaced there as "52%" (in the
    // score disclosure since P3).
    openScore()
    expect(screen.queryByText(/headline 52% pass rate/)).not.toBeInTheDocument()
    expect(screen.getByText(/headline 57% pass rate/)).toBeInTheDocument()
  })

  // Regression: test executions were labelled "runs" — a 6-run window read
  // "31 / 60 runs". Same wording defect #643 fixed on the dashboard.
  it('calls test executions executions, not runs', async () => {
    const { useTrendData, useDashboardSummary, useCoverage } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        data: [
          { date: '2026-08-16', passed: 31, failed: 17, skipped: 6, broken: 6, total: 60, pass_rate: 57.4 },
        ],
      },
      isLoading: false,
    })
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({ data: {} })
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        summary: { unique_tests: 10, suite_count: 1, total_executions: 60, avg_pass_rate: 57.4, days_with_runs: 1 },
        suites: [{ suite_name: 'GroundTruthSuite', unique_tests: 10, passed: 31, failed: 23, skipped: 6, pass_rate: 57.4 }],
      },
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })

    // The suite pass rates are the By suite tab's since P3.
    render(
      <MemoryRouter initialEntries={['/trends?tab=by-suite']}>
        <Routes>
          <Route path="/trends" element={<TrendsPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/31 \/ 54 evaluated/)).toBeInTheDocument()
    expect(screen.queryByText(/31 \/ 60 runs/)).not.toBeInTheDocument()
    expect(screen.queryByText(/of 60 runs/)).not.toBeInTheDocument()
  })

  // Regression: the axis tick labelled "(today)" named yesterday. shortDate()
  // ran `new Date('2026-08-16')`, which parses as UTC midnight, so every date
  // on the page rendered a day early west of Greenwich — the heatmap, the
  // daily-breakdown axis and the "scheduler paused since …" narrative included.
  it('labels the window in the same calendar frame the API buckets in', async () => {
    const { useTrendData, useDashboardSummary, useCoverage } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')
    const { formatDayIso, shiftDayIso, utcDayIso } = await import('@/utils/calendarDay')

    const todayIso = utcDayIso()
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [{ date: todayIso, passed: 31, failed: 17, skipped: 6, broken: 6, total: 60, pass_rate: 57.4 }] },
      isLoading: false,
    })
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({ data: {} })
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        summary: { unique_tests: 10, suite_count: 1, total_executions: 60, avg_pass_rate: 57.4, days_with_runs: 1 },
        suites: [{ suite_name: 'GroundTruthSuite', unique_tests: 10, passed: 31, failed: 23, skipped: 6, pass_rate: 57.4 }],
      },
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })

    render(
      <MemoryRouter initialEntries={['/trends']}>
        <Routes>
          <Route path="/trends" element={<TrendsPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await screen.findByRole('heading', { name: 'Trends' })
    // The cell the cadence strip marks as today must BE today, not the day
    // before (the strip's table is where a reader gets the per-day truth).
    const rows = cadenceRows()
    expect(rows).toHaveLength(14)
    expect(rows[13]).toEqual([`${todayIso} · 60 executions (17 failed, 6 broken)`, 'Runs with failures', 'Today'])
    expect(rows.filter((r) => r[2] === 'Today')).toHaveLength(1)
    // The 14-day window opens 13 days back, and that day must be right too.
    expect(rows[0][0]).toBe(`${shiftDayIso(todayIso, -13)} · 0 executions`)
    // The daily breakdown's columns are the same 14 UTC days, labelled in the same frame.
    const buckets = lastStacked().model.buckets
    expect(buckets.map((b) => b.key)).toEqual(rows.map((r) => r[0].slice(0, 10)))
    expect(buckets[13].label).toBe(formatDayIso(todayIso))
  })
})

// VIZ-104 (Wave 2.5): the page's hand-drawn visuals moved onto the chart kit.
// One 14-day window exercises every claim: today has no runs (so the strip
// marks the last active day as the gap edge), yesterday is mixed, a day with
// only BROKEN failures, a day of only skips, a clean day, and a 6-day silence.
describe('TrendsPage on the chart kit', () => {
  beforeEach(() => {
    analyticsControls.widgetIds = []
    frames.stacked.length = 0
    frames.timeSeries.length = 0
  })

  async function renderWindow(trend: unknown[]) {
    const { useTrendData, useDashboardSummary, useCoverage } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({ data: { data: trend }, isLoading: false })
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({ data: {} })
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        summary: { unique_tests: 10, suite_count: 2, total_executions: 97, avg_pass_rate: 70, days_with_runs: 4 },
        suites: [
          { suite_name: 'Checkout', unique_tests: 6, passed: 40, failed: 10, skipped: 5, pass_rate: 80 },
          { suite_name: 'Payments', unique_tests: 4, passed: 21, failed: 21, skipped: 0, pass_rate: 50 },
        ],
      },
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    render(
      <MemoryRouter initialEntries={['/trends']}>
        <Routes><Route path="/trends" element={<TrendsPage />} /></Routes>
      </MemoryRouter>,
    )
    await screen.findByRole('heading', { name: 'Trends' })
  }

  async function windowFixture() {
    const { shiftDayIso, utcDayIso } = await import('@/utils/calendarDay')
    const today = utcDayIso()
    const day = (back: number) => shiftDayIso(today, -back)
    return {
      today,
      day,
      trend: [
        { date: day(1), passed: 31, failed: 17, skipped: 6, broken: 6, total: 60, pass_rate: 57.4 },
        // Broken but nothing "failed": still a day with failures.
        { date: day(3), passed: 10, failed: 0, skipped: 0, broken: 2, total: 12, pass_rate: 83.3 },
        // Only skips: executions happened, but no pass rate was measured.
        { date: day(5), passed: 0, failed: 0, skipped: 5, broken: 0, total: 5, pass_rate: 0 },
        // A payload date that carries a time still belongs to its UTC day.
        { date: `${day(7)}T00:00:00Z`, passed: 20, failed: 0, skipped: 0, broken: 0, total: 20, pass_rate: 100 },
      ],
    }
  }

  it('draws the Pass rate and Executions sparklines from the per-day series: a quiet day breaks the rate, and ran 0', async () => {
    const { trend } = await windowFixture()
    await renderWindow(trend)
    const kpis = screen.getByRole('region', { name: 'Trend metrics' })
    // 14 days: 3 with an evaluated execution, 4 with any execution.
    expect(within(kpis).getByRole('img', { name: /^Pass rate per day:/ })).toHaveAccessibleName(
      'Pass rate per day: 3 points, latest 57.4%, min 57.4%, max 100.0%, 11 not measured',
    )
    // R2 F3: a day with no runs is a real 0 executions, as on Overview, never
    // "not measured": today ran nothing, so the latest value is 0.
    expect(within(kpis).getByRole('img', { name: /^Executions per day:/ })).toHaveAccessibleName(
      'Executions per day: 14 points, latest 0, min 0, max 60',
    )
  })

  it('draws the Executions sparkline from zero, as Overview draws the same measure (R1 F1)', async () => {
    const { day } = await windowFixture()
    // Every day ran 104 executions, and the newest 100: a 4 % dip. On its own
    // [min, max] that dip is the full height of the cell.
    const trend = Array.from({ length: 14 }, (_, i) => {
      const n = i === 0 ? 100 : 104
      return { date: day(i), passed: n, failed: 0, skipped: 0, broken: 0, total: n, pass_rate: 100 }
    })
    await renderWindow(trend)
    const kpis = screen.getByRole('region', { name: 'Trend metrics' })
    const spark = within(kpis).getByRole('img', { name: /^Executions per day:/ })
    const dot = spark.querySelector('[data-part="end-dot"]') as HTMLElement
    // The newest point sits near the top (100 of 104 on a zero-based scale), not on the floor.
    expect(parseFloat(dot.style.top)).toBeLessThan(8)
  })

  it('draws no Executions line around a single day with runs', async () => {
    const { day } = await windowFixture()
    await renderWindow([{ date: day(2), passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 }])
    const kpis = screen.getByRole('region', { name: 'Trend metrics' })
    expect(within(kpis).queryByRole('img', { name: /^Executions per day:/ })).toBeNull()
  })

  it('draws no glyph in the three KPI cells that had only decoration', async () => {
    const { trend } = await windowFixture()
    await renderWindow(trend)
    const kpis = screen.getByRole('region', { name: 'Trend metrics' })
    // The only pictures in the strip are the two real sparklines.
    expect(within(kpis).getAllByRole('img')).toHaveLength(2)
    for (const label of ['Days with runs', 'Suites', 'Last run']) {
      // P3: a compact KPI tile (`MetricCard`) per cell.
      const cell = within(kpis).getByText(label).closest('[data-metric-card]') as HTMLElement
      expect(cell).not.toBeNull()
      expect(cell.querySelector('svg:not(.lucide), [role="img"]')).toBeNull()
    }
  })

  it('marks failing days on the cadence strip — broken included — and the gap edge before a quiet today', async () => {
    const { trend, today, day } = await windowFixture()
    await renderWindow(trend)
    const rows = cadenceRows()
    expect(rows).toHaveLength(14)
    const byDay = new Map(rows.map((r) => [r[0].slice(0, 10), r]))
    expect(byDay.get(day(1))).toEqual([`${day(1)} · 60 executions (17 failed, 6 broken)`, 'Runs with failures', 'Last day with runs before a gap'])
    expect(byDay.get(day(3))).toEqual([`${day(3)} · 12 executions (2 broken)`, 'Runs with failures', ''])
    expect(byDay.get(day(5))?.[1]).toBe('Runs')
    expect(byDay.get(day(7))?.[1]).toBe('Runs')
    expect(byDay.get(today)).toEqual([`${today} · 0 executions`, 'No runs', 'Today'])
    expect(rows.filter((r) => r[1] === 'Runs with failures')).toHaveLength(2)
    expect(screen.getByRole('img', { name: /^Run cadence:/ })).toHaveAccessibleName(
      'Run cadence: 10 empty days, 4 days with executions, 2 with failures',
    )
    // R1 F7: the oldest of 14 days ending today is 13 days ago.
    const legend = screen.getByRole('img', { name: /^Run cadence:/ }).closest('[data-day-strip]')?.querySelector('[data-day-strip-legend]')
    expect(legend).toHaveTextContent(/^13 days ago/)
  })

  it('draws the confidence score on the kit meter', async () => {
    const { trend } = await windowFixture()
    await renderWindow(trend)
    openScore()
    const meter = screen.getByRole('meter', { name: 'Trend confidence' })
    expect(meter).toHaveAttribute('aria-valuemin', '0')
    expect(meter).toHaveAttribute('aria-valuemax', '100')
    expect(meter.getAttribute('aria-valuetext')).toMatch(/^\d+ of 100, (Low|Moderate|High)$/)
  })

  it('shows a PENDING window as not measured on the meter, never a 0 score', async () => {
    await renderWindow([])
    // The disclosure's header says so too: no score.
    expect(screen.getByRole('button', { name: /How this score is computed/ })).toHaveTextContent('Pending · no score yet')
    openScore()
    expect(screen.queryByRole('meter', { name: 'Trend confidence' })).toBeNull()
    expect(screen.getByRole('img', { name: 'Trend confidence: not measured' })).toBeInTheDocument()
  })

  it('hands the daily breakdown four statuses, broken counted, Skipped and Broken apart, a quiet day a measured 0', async () => {
    const { trend, today, day } = await windowFixture()
    await renderWindow(trend)
    const { model, title, takeaway } = lastStacked()
    expect(title).toBe('Daily breakdown')
    // R2 F4: the kit's one stack order, as on Overview and SuiteDetail.
    expect(model.series.map((s) => [s.key, s.status])).toEqual([
      ['passed', 'passed'],
      ['failed', 'failed'],
      ['broken', 'broken'],
      ['skipped', 'skipped'],
    ])
    const skipped = model.series.find((s) => s.key === 'skipped')
    const broken = model.series.find((s) => s.key === 'broken')
    expect(skipped?.color).not.toBe(broken?.color)
    const bucket = (iso: string) => model.buckets.find((b) => b.key === iso)
    expect(bucket(day(1))?.values).toEqual([31, 17, 6, 6])
    expect(bucket(day(1))?.total).toBe(60)
    expect(bucket(day(5))?.values).toEqual([0, 0, 0, 5]) // passed, failed, broken, skipped
    // The timestamped payload date still lands on its UTC day.
    expect(bucket(day(7))?.values).toEqual([20, 0, 0, 0])
    // R2 F3: a day the payload never sent had no runs: it ran nothing, a
    // measured 0 (a tick on the baseline), as on Overview and SuiteDetail. It
    // is never "no measured value".
    expect(bucket(today)?.values).toEqual([0, 0, 0, 0])
    expect(bucket(today)?.zero).toBe(true)
    expect(model.gaps).toBe(0)
    // R2 F5: the kit's UTC day label, as every other day chart on the page.
    expect(bucket(today)?.label).toBe(utcDayLabel(today))
    expect(takeaway).toBe('61 passed · 17 failed · 8 broken · 11 skipped over the last 14 days')
    expect(screen.getByRole('heading', { name: 'Daily breakdown' })).toBeInTheDocument()
  })

  // R1 F3: a new project whose only run is still streaming has one day of all
  // zeros and no other day. With no filter set, that is not "No data matches
  // the current filters": the frame states the window, neutrally.
  it('says "No executions in this window" for an all-zero window with no filter set', async () => {
    const { today } = await windowFixture()
    await renderWindow([{ date: today, passed: 0, failed: 0, skipped: 0, broken: 0, total: 0, pass_rate: 0 }])
    expect(lastStacked().filtersApplied).toBe(false)
    const frame = screen.getByRole('heading', { name: 'Daily breakdown' }).closest('[data-chart-frame]') as HTMLElement
    expect(frame).toHaveTextContent('No executions in this window')
    expect(frame).not.toHaveTextContent('No data matches the current filters')
  })

  it('tells the frame a suite filter is set, so an empty window keeps the filter words', async () => {
    const { today } = await windowFixture()
    await renderWindow([{ date: today, passed: 0, failed: 0, skipped: 0, broken: 0, total: 0, pass_rate: 0 }])
    fireEvent.change(screen.getByDisplayValue('All suites'), { target: { value: 'Checkout' } })
    expect(lastStacked().filtersApplied).toBe(true)
  })

  it('draws the pass-rate trend per day with the 90% target, a skip-only or quiet day a gap', async () => {
    const { trend, today, day } = await windowFixture()
    await renderWindow(trend)
    const { model, rateTarget, title, takeaway } = lastTimeSeries()
    expect(title).toBe('Pass rate trend')
    expect(rateTarget).toEqual({ value: 90, label: 'Target 90%' })
    expect(model.points).toHaveLength(14)
    const rate = (iso: string) => model.points.find((pt) => pt.x === iso)?.rate
    expect(rate(day(1))).toBe(57.4)
    expect(rate(day(7))).toBe(100)
    expect(rate(day(5))).toBeNull()
    expect(rate(today)).toBeNull()
    // The headline is the WINDOW's rate: 61 of 86 evaluated (skips outside it).
    expect(takeaway).toBe('70.9% over the last 14 days, 19 pp below the 90% target')
    expect(screen.getByRole('heading', { name: 'Pass rate trend' })).toBeInTheDocument()
  })

  it('says a window with too few measured days has no trend', async () => {
    const { day } = await windowFixture()
    await renderWindow([{ date: day(2), passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 }])
    expect(lastTimeSeries().takeaway).toBe('90.0% over the last 14 days, at the 90% target · 1 day with data, too few for a trend')
  })

  // TRND-06: the fake 6-tick micro-bar never came back; the row now draws the
  // suite's REAL daily pass rate (the sparkline, its own test below).
  it('has no per-suite micro-bar: a suite row is the name (with its links) over its line, and its rate', async () => {
    const { trend } = await windowFixture()
    await renderWindow(trend)
    openTab('By suite')
    const suites = screen.getByRole('heading', { name: 'Suite pass rates — last 14 days' }).closest('div.card') as HTMLElement
    const row = within(suites).getByRole('link', { name: 'Payments' }).closest('div.grid') as HTMLElement
    expect(row).toHaveTextContent('50%')
    expect(row.querySelectorAll('i')).toHaveLength(0)
    expect(row.children).toHaveLength(2)
    // UX redesign P4: the name opens the suite's own page (by name, resolved to its id).
    expect(within(suites).getByRole('link', { name: 'Payments' })).toHaveAttribute('href', '/coverage/suite?name=Payments')
  })
})

// Wave 2.6 (VIZ-408 + VIZ-106): the catalogue, the one-request window, and the
// page's own responsive rework.
describe('TrendsPage rollout (Wave 2.6)', () => {
  beforeEach(async () => {
    analyticsControls.widgetIds = []
    frames.stacked.length = 0
    frames.timeSeries.length = 0
    catalogue.props.length = 0
    catalogue.throws = false
    releaseList.items = []
    const { useTimeWindowStore } = await import('@/store/timeWindowStore')
    // The app-wide default: what a reader arriving from any other page holds.
    useTimeWindowStore.setState({ days: 30 })
    const metrics = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')
    const { useSuiteOptions } = await import('@/hooks/useSuiteOptions')
    for (const hook of [metrics.useTrendData, metrics.useDashboardSummary, metrics.useCoverage, useRuns, useSuiteOptions]) {
      ;(hook as ReturnType<typeof vi.fn>).mockClear()
    }
  })

  /** `back` days before today, as a UTC day. */
  async function dayBack(back: number) {
    const { shiftDayIso, utcDayIso } = await import('@/utils/calendarDay')
    return shiftDayIso(utcDayIso(), -back)
  }

  /** `count` consecutive days with runs, ending yesterday, each at `rate`. */
  async function activeDays(count: number, rate = 90) {
    return Promise.all(
      Array.from({ length: count }, async (_, i) => ({
        date: await dayBack(i + 1), passed: rate, failed: 100 - rate, skipped: 0, broken: 0, total: 100, pass_rate: rate,
      })),
    )
  }

  async function renderTrends(trend: unknown[]) {
    const { useTrendData, useDashboardSummary, useCoverage } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({ data: { data: trend }, isLoading: false })
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({ data: {} })
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({ data: { summary: {}, suites: [] } })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    const view = render(
      <MemoryRouter initialEntries={['/trends']}>
        <Routes><Route path="/trends" element={<TrendsPage />} /></Routes>
      </MemoryRouter>,
    )
    await screen.findByRole('heading', { name: 'Trends' })
    return view
  }

  const lastCatalogue = () => catalogue.props[catalogue.props.length - 1]

  // C0 pinned it (before-notes 13): the reset to 14 days was an effect, so the
  // first render had already asked for the stored 30, and every page read went
  // out twice on a cold load.
  it('asks for its 14-day window on the first render: no page read at the stored 30 days first', async () => {
    await renderTrends(await activeDays(3))
    const metrics = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')
    const { useSuiteOptions } = await import('@/hooks/useSuiteOptions')
    const { useTimeWindowStore } = await import('@/store/timeWindowStore')
    for (const hook of [metrics.useTrendData, metrics.useDashboardSummary, metrics.useCoverage, useSuiteOptions]) {
      const calls = (hook as ReturnType<typeof vi.fn>).mock.calls
      expect(calls.length).toBeGreaterThan(0)
      expect(new Set(calls.map((call) => call[0]))).toEqual(new Set([14]))
    }
    const runCalls = (useRuns as ReturnType<typeof vi.fn>).mock.calls
    expect(runCalls.length).toBeGreaterThan(0)
    expect(new Set(runCalls.map((call) => (call[0] as { days: number }).days))).toEqual(new Set([14]))
    // The shared preference is still reset, so other pages follow.
    expect(useTimeWindowStore.getState().days).toBe(14)
    expect(windowOption('14d')).toHaveAttribute('aria-checked', 'true')
  })

  it('still follows the window picker after the reset', async () => {
    await renderTrends(await activeDays(3))
    const { useTrendData } = await import('@/hooks/useMetrics')
    fireEvent.click(windowOption('90d'))
    const calls = (useTrendData as ReturnType<typeof vi.fn>).mock.calls
    expect(calls[calls.length - 1][0]).toBe(90)
    expect(windowOption('90d')).toHaveAttribute('aria-checked', 'true')
  })

  it('the pass-rate frame offers the trend overlays, the brush and the release markers', async () => {
    releaseList.items = [
      { id: 'r1', name: 'R1', released_at: await dayBack(2), planned_date: null },
      { id: 'r2', name: 'R2', released_at: null, planned_date: await dayBack(4) },
      { id: 'r3', name: 'R3', released_at: null, planned_date: null },
    ]
    await renderTrends(await activeDays(10))
    const section = document.querySelector('[data-catalogue-section="trends-pass-rate"]') as HTMLElement
    expect(section).not.toBeNull()
    // The frame keeps its title as its heading, inside the section.
    expect(within(section).getByRole('heading', { name: 'Pass rate trend' })).toBeInTheDocument()
    const { trendAnalysis, zoom, rateTarget, model } = lastTimeSeries()
    expect(trendAnalysis).toBe(true)
    expect(zoom).toBe(true)
    expect(rateTarget).toEqual({ value: 90, label: 'Target 90%' })
    // Released, else planned; an undated release is not drawn.
    expect(model.markers.flatMap((m) => m.names).sort()).toEqual(['R1', 'R2'])
    // 10 days with runs: the overlays are offered and not disabled.
    const controls = section.querySelector('[data-trend-controls]') as HTMLElement
    expect(controls).not.toBeNull()
    expect(controls.querySelectorAll('[data-trend-toggle]').length).toBeGreaterThan(0)
    expect(controls.querySelector('[aria-disabled="true"]')).toBeNull()
  })

  it('at 7 days with fewer than 7 days with runs, offers the overlays disabled with the reason', async () => {
    await renderTrends(await activeDays(4))
    fireEvent.click(windowOption('7d'))
    const section = document.querySelector('[data-catalogue-section="trends-pass-rate"]') as HTMLElement
    const toggles = [...section.querySelectorAll('[data-trend-toggle]')]
    expect(toggles.length).toBeGreaterThan(0)
    for (const toggle of toggles) expect(toggle).toHaveAttribute('aria-disabled', 'true')
    expect(section.querySelector('[data-trend-disabled-reason]')).toHaveTextContent('Needs at least 7 days with runs')
    openTab('By suite')
    await screen.findByTestId('trends-catalogue')
    expect(lastCatalogue().days).toBe(7)
  })

  it('mounts the catalogue with the page window and suite scope, in its tab, after the hero', async () => {
    await renderTrends(await activeDays(3))
    // P3: the catalogue is in the By suite, Execution time and Heatmap tabs; the default tab (Volume) does not mount it.
    expect(screen.queryByTestId('trends-catalogue')).toBeNull()
    expect(catalogue.props).toEqual([])
    openTab('By suite')
    await screen.findByTestId('trends-catalogue')
    expect(lastCatalogue()).toEqual({ days: 14, suiteFilter: null, sections: ['trends-multi-series', 'trends-compare'] })
    fireEvent.change(screen.getByDisplayValue('All suites'), { target: { value: 'Checkout' } })
    expect(lastCatalogue()).toEqual({ days: 14, suiteFilter: 'Checkout', sections: ['trends-multi-series', 'trends-compare'] })
    const hero = document.querySelector('[data-primary]') as HTMLElement
    const stub = screen.getByTestId('trends-catalogue')
    expect(stub.closest('[data-catalogue-scope]')).toHaveAttribute('data-catalogue-scope', 'by-suite')
    expect(hero.compareDocumentPosition(stub) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.queryByText('Provenance')).toBeNull()
  })

  // R1-1: the section chunk failing to load, or the section throwing, is the
  // section's own error state. Before, the nearest boundary was the route's,
  // so the whole page became "Something went wrong loading this page".
  it('a section that throws leaves the page and shows the section error', async () => {
    catalogue.throws = true
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    try {
      await renderTrends(await activeDays(3))
      openTab('By suite')
      expect(await screen.findByText('Could not load charts')).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: 'Trends' })).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: 'Pass rate trend' })).toBeInTheDocument()
    } finally {
      spy.mockRestore()
    }
  })

  // The catalogue gets the window the page SHOWS (snapped to its options),
  // never the raw shared preference: 45 days picked elsewhere reads as 30
  // here, and the charts must say 30 like every other number on the page.
  it('hands the catalogue the snapped page window, not the raw stored one', async () => {
    await renderTrends(await activeDays(3))
    openTab('Execution time')
    await screen.findByTestId('trends-catalogue')
    const { useTimeWindowStore } = await import('@/store/timeWindowStore')
    act(() => useTimeWindowStore.getState().setDays(45))
    expect(windowOption('30d')).toHaveAttribute('aria-checked', 'true')
    expect(lastCatalogue().days).toBe(30)
  })

  it('is one column below 1024 px: the By suite grid and the score disclosure are fluid, with no fixed columns', async () => {
    await renderTrends(await activeDays(3))
    openTab('By suite')
    const grid = (await screen.findByTestId('trends-catalogue')).closest('[data-catalogue-scope]')?.parentElement as HTMLElement
    expect(grid).toHaveClass('grid-cols-1', 'lg:grid-cols-[minmax(0,1fr)_minmax(0,340px)]')
    expect(grid.style.gridTemplateColumns).toBe('')
    openScore()
    const verdict = screen.getByRole('region', { name: 'Trend verdict' })
    expect(verdict).toHaveClass('grid-cols-1', 'lg:grid-cols-[1.45fr_1fr]')
    expect(verdict.style.gridTemplateColumns).toBe('')
  })

  it('no longer apologises for narrow screens', async () => {
    await renderTrends(await activeDays(3))
    expect(screen.queryByText(/Wider screen needed/)).toBeNull()
  })

  // P1 (2026-10-04): the header's own Views button opens its actions. P2: the
  // Customize button that followed it (the widget picker) is gone. P3: the
  // header's right side is the page's one filter row: the suite, the shared
  // window picker, then Views (the one secondary button), and nothing else.
  it('has the suite filter, the window picker and the Views button in the header, and no Customize button', async () => {
    await renderTrends(await activeDays(3))
    const header = document.querySelector('[data-page-header]') as HTMLElement
    const toolbar = header.querySelector('[data-page-toolbar]') as HTMLElement
    const trigger = toolbar.querySelector('[data-saved-views-trigger]') as HTMLElement
    expect(trigger.textContent).toBe('Views')
    const suite = within(toolbar).getByDisplayValue('All suites')
    const windows = within(toolbar).getByRole('radiogroup', { name: 'Time window' })
    const follows = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
    expect(follows(suite, windows) && follows(windows, trigger)).toBe(true)
    expect(within(windows).getAllByRole('radio').map((r) => r.textContent)).toEqual(['24h', '7d', '14d', '30d', '90d'])
    // The help ? opens the page's docs topic.
    expect(within(header).getByRole('button', { name: 'Help: Trends' })).toHaveAttribute('data-help-topic', 'dashboards')
    // W08: the ⋯ (Export PDF, Email report, Compare runs) follows Views in the
    // same action row: one 32 px trigger, no row of its own.
    const overflow = header.querySelector('[data-overflow-trigger]') as HTMLElement
    expect(overflow).not.toBeNull()
    expect(follows(trigger, overflow)).toBe(true)
    expect(overflow.closest('[data-page-toolbar]')).toBeNull()
    expect(screen.queryByRole('button', { name: /Customize/ })).toBeNull()
    expect(screen.queryByRole('dialog')).toBeNull()
  })
})

// P2 "remove the noise" (UX redesign): anything not built is not rendered.
// Every control below only raised a not-built toast; every value below was
// invented. Each test fails if its item comes back.
describe('TrendsPage P2: no stubs, no invented values', () => {
  beforeEach(async () => {
    analyticsControls.widgetIds = []
    frames.stacked.length = 0
    frames.timeSeries.length = 0
    const { useTimeWindowStore } = await import('@/store/timeWindowStore')
    useTimeWindowStore.setState({ days: 14 })
  })

  type Suite = { suite_name: string; unique_tests: number; passed: number; failed: number; skipped: number; pass_rate: number }

  async function renderWith(trend: unknown[], suites: Suite[] = [
    { suite_name: 'Checkout', unique_tests: 6, passed: 40, failed: 10, skipped: 5, pass_rate: 80 },
  ]) {
    const { useTrendData, useDashboardSummary, useCoverage } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({ data: { data: trend }, isLoading: false })
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({ data: {} })
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({ data: { summary: {}, suites } })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    render(
      <MemoryRouter initialEntries={['/trends']}>
        <Routes><Route path="/trends" element={<TrendsPage />} /></Routes>
      </MemoryRouter>,
    )
    await screen.findByRole('heading', { name: 'Trends' })
  }

  /** `back` days before today, as a UTC day. */
  async function dayBack(back: number) {
    const { shiftDayIso, utcDayIso } = await import('@/utils/calendarDay')
    return shiftDayIso(utcDayIso(), -back)
  }

  /** One day with runs per `backs` entry. */
  async function daysWithRuns(backs: number[]) {
    return Promise.all(backs.map(async (back) => ({
      date: await dayBack(back), passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90,
    })))
  }

  // W08 built four of the controls this test once proved absent: Export PDF,
  // Email report, Compare runs (the header ⋯) and pinning a note (the cadence
  // card and the gap callout). Each now EXISTS and has its own tests below;
  // the controls that are still not built stay absent.
  it('has none of the not-built controls: header, verdict, issue rows, schedule callout', async () => {
    // A trailing 10-day silence: the schedule callout and the gap issue row both render.
    await renderWith(await daysWithRuns([10, 11, 12, 13]))
    expect(screen.getByRole('heading', { name: 'Schedule appears paused' })).toBeInTheDocument()
    for (const name of [
      /Email this view/, /Resume schedule/, /Resume nightly/, /View schedule/, /Decision trail/, /Customize/,
      /Configure schedule/, /Apply suite labels/, /Open runner config/,
    ]) {
      expect(screen.queryByRole('button', { name })).toBeNull()
      expect(screen.queryByRole('link', { name })).toBeNull()
      expect(screen.queryByRole('menuitem', { name })).toBeNull()
    }
    // P3: the verdict's one real action (widening the window) was the window
    // picker's 90d again; the picker stays, the duplicate is gone.
    expect(screen.queryByRole('button', { name: /Widen/ })).toBeNull()
    expect(windowOption('90d')).toHaveAttribute('aria-checked', 'false')
  })

  it('W08: the built ones exist — the ⋯ holds Export PDF and Compare runs; a viewer is offered no Email report and no Pin a note', async () => {
    await renderWith(await daysWithRuns([10, 11, 12, 13]))
    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    expect(screen.getByRole('menuitem', { name: 'Export PDF' })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: 'Compare runs' })).toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: /Email report/ })).toBeNull()
    expect(screen.queryByRole('button', { name: /Pin a note/ })).toBeNull()
  })

  it('renders no recommended-actions card and no provenance footer', async () => {
    await renderWith(await daysWithRuns([1, 2, 3]))
    expect(screen.queryByRole('heading', { name: 'Recommended actions' })).toBeNull()
    expect(screen.queryByText(/routed by role/)).toBeNull()
    expect(screen.queryByText(/today's failures/)).toBeNull()
    expect(screen.queryByText('Provenance')).toBeNull()
    expect(screen.queryByText(/2 tools/)).toBeNull()
    expect(screen.queryByText(/trends analyzer v1/)).toBeNull()
  })

  it('titles the suite pass rates by the window it reads, never "today" or "single day"', async () => {
    await renderWith(await daysWithRuns([1, 2, 3]))
    openTab('By suite')
    expect(screen.getByRole('heading', { name: 'Suite pass rates — last 14 days' })).toBeInTheDocument()
    expect(screen.queryByText(/Suite pass rates · today/)).toBeNull()
    expect(screen.queryByText('Single day')).toBeNull()
    expect(screen.queryByText(/No delta available — single day of data/)).toBeNull()
    // The window it reads is the window picked: the coverage read and the title move together.
    fireEvent.click(windowOption('90d'))
    expect(screen.getByRole('heading', { name: 'Suite pass rates — last 90 days' })).toBeInTheDocument()
    const { useCoverage } = await import('@/hooks/useMetrics')
    const calls = (useCoverage as ReturnType<typeof vi.fn>).mock.calls
    expect(calls[calls.length - 1][0]).toBe(90)
    fireEvent.click(windowOption('24h'))
    expect(screen.getByRole('heading', { name: 'Suite pass rates — last 24 hours' })).toBeInTheDocument()
  })

  it('says how many suites the card shows when it shows fewer than there are', async () => {
    const suites = Array.from({ length: 8 }, (_, i) => ({
      suite_name: `S${i}`, unique_tests: 1, passed: 9, failed: 1, skipped: 0, pass_rate: 90,
    }))
    await renderWith(await daysWithRuns([1, 2, 3]), suites)
    openTab('By suite')
    const card = screen.getByRole('heading', { name: /^Suite pass rates/ }).closest('div.card') as HTMLElement
    const rows = within(card).getAllByText(/^S\d$/)
    expect(rows).toHaveLength(6)
    expect(card).toHaveTextContent(`${rows.length} of 8 suites`)
  })

  it('calls an older silence a past gap, not a paused schedule', async () => {
    // Runs on the last 3 days; a 9-day silence before them.
    await renderWith(await daysWithRuns([0, 1, 2, 12, 13]))
    expect(screen.getByRole('heading', { name: 'Run gap in this window' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Schedule appears paused' })).toBeNull()
    expect(screen.queryByText(/hasn't fired since/)).toBeNull()
    expect(screen.queryByText(/appears paused since/)).toBeNull()
    // The callout states the past silence's dates (the verdict's issue row
    // that said it again went with the verdict card, P3).
    expect(screen.getByText(/No executions landed from/)).toBeInTheDocument()
  })

  it("states the window's executions as the window's, with no invented regression claim", async () => {
    await renderWith(await daysWithRuns([1, 2, 3]))
    // P3: the Executions tile states them (the verdict's issue row repeated it).
    const tile = within(screen.getByRole('region', { name: 'Trend metrics' })).getByText('Executions').closest('[data-metric-card]') as HTMLElement
    expect(tile).toHaveTextContent('30')
    expect(tile).toHaveTextContent('27 passed · 3 failed · 0 broken · 0 skipped')
    expect(screen.queryByText(/The latest active day had/)).toBeNull()
    expect(screen.queryByText(/No regression vs\. the prior in-window run/)).toBeNull()
  })
})

// P3 (UX redesign, the page template): header · KPI strip · the hero (the
// pass-rate trend, `data-primary`) · the score disclosure · the page's tabs
// (Volume · By suite · Durations · Heatmap, in `?tab=`). Every chart was
// moved, none rebuilt.
describe('TrendsPage P3: the page template', () => {
  beforeEach(async () => {
    frames.stacked.length = 0
    frames.timeSeries.length = 0
    catalogue.props.length = 0
    catalogue.throws = false
    const { useTimeWindowStore } = await import('@/store/timeWindowStore')
    useTimeWindowStore.setState({ days: 14 })
  })

  /** The router's location, rendered so a test can read the URL the page writes. */
  function Location() {
    const location = useLocation()
    return <output data-testid="location">{`${location.pathname}${location.search}`}</output>
  }

  async function renderAt(path: string) {
    const { useTrendData, useDashboardSummary, useCoverage } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')
    const { shiftDayIso, utcDayIso } = await import('@/utils/calendarDay')
    const trend = [1, 2, 3, 4].map((back) => ({
      date: shiftDayIso(utcDayIso(), -back), passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90,
    }))
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({ data: { data: trend }, isLoading: false })
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({ data: {} })
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { summary: {}, suites: [{ suite_name: 'Checkout', unique_tests: 6, passed: 40, failed: 10, skipped: 5, pass_rate: 80 }] },
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    render(
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path="/trends" element={<><TrendsPage /><Location /></>} /></Routes>
      </MemoryRouter>,
    )
    await screen.findByRole('heading', { name: 'Trends' })
  }

  const follows = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)

  it('puts the one primary content (the pass-rate hero) before the page tab bar and the disclosure', async () => {
    await renderAt('/trends')
    const primary = document.querySelectorAll('[data-primary]')
    expect(primary).toHaveLength(1)
    expect(within(primary[0] as HTMLElement).getByRole('heading', { name: 'Pass rate trend' })).toBeInTheDocument()
    // After the header and the KPI strip.
    expect(follows(screen.getByRole('region', { name: 'Trend metrics' }), primary[0])).toBe(true)
    const tablists = screen.getAllByRole('tablist')
    expect(tablists.map((t) => t.getAttribute('aria-label'))).toEqual(['Trend views'])
    const disclosures = document.querySelectorAll('[data-disclosure]')
    expect(disclosures).toHaveLength(1)
    for (const later of [...tablists, ...disclosures]) expect(follows(primary[0], later)).toBe(true)
    // Five compact KPI tiles at most, in one strip.
    const strip = within(screen.getByRole('region', { name: 'Trend metrics' }))
    expect(strip.getAllByText(/^(Pass rate|Days with runs|Executions|Suites|Last run)$/)).toHaveLength(5)
    expect(document.querySelectorAll('[data-kpi-strip] [data-metric-card="compact"]')).toHaveLength(5)
  })

  it('keeps the score collapsed: its header states the verdict and the score, and it opens to the meter and the dimensions', async () => {
    await renderAt('/trends')
    const toggle = screen.getByRole('button', { name: /How this score is computed/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle.textContent).toMatch(/Trend (healthy|mixed|declining) · confidence \d+ \/ 100|Insufficient data · confidence \d+ \/ 100/)
    expect(screen.queryByRole('meter', { name: 'Trend confidence' })).toBeNull()
    openScore()
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('meter', { name: 'Trend confidence' })).toBeInTheDocument()
    for (const dim of ['Data coverage', 'Sample size', 'Variance stability', 'Tag quality']) {
      expect(within(screen.getByRole('region', { name: 'Trend verdict' })).getByText(dim)).toBeInTheDocument()
    }
  })

  it('opens on Volume with a clean URL: the cadence strip, the daily breakdown, and no catalogue', async () => {
    await renderAt('/trends')
    expect(within(screen.getByRole('tablist', { name: 'Trend views' })).getByRole('tab', { name: 'Volume' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('heading', { name: 'Daily breakdown' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Run cadence — last 14 days' })).toBeInTheDocument()
    // P3: the cadence explainer is the title's tooltip, not a paragraph on the page.
    expect(screen.getByRole('heading', { name: 'Run cadence — last 14 days' })).toHaveAttribute('title', expect.stringMatching(/^Each cell is one day\./))
    expect(screen.queryByText(/the schedule, the runner, or someone with a manual trigger/)).toBeNull()
    expect(screen.queryByTestId('trends-catalogue')).toBeNull()
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/trends$/)
  })

  it.each([
    ['volume', 'Volume'],
    ['by-suite', 'By suite'],
    // EXEC-04b: the tab is labelled "Execution time"; its id (and every link to it) is still `durations`.
    ['durations', 'Execution time'],
    ['heatmap', 'Heatmap'],
  ] as const)('?tab=%s selects %s and renders its section', async (id, label) => {
    await renderAt(`/trends?tab=${id}`)
    expect(within(screen.getByRole('tablist', { name: 'Trend views' })).getByRole('tab', { name: label })).toHaveAttribute('aria-selected', 'true')
    const panel = document.querySelector('[data-tab-panel]') as HTMLElement
    expect(panel).toHaveAttribute('data-tab-panel', id)
    if (id === 'volume') {
      expect(within(panel).getByRole('heading', { name: 'Daily breakdown' })).toBeInTheDocument()
      expect(within(panel).queryByTestId('trends-catalogue')).toBeNull()
      return
    }
    // The catalogue, scoped to this tab's sections.
    const stub = await within(panel).findByTestId('trends-catalogue')
    expect(stub.closest('[data-catalogue-scope]')).toHaveAttribute('data-catalogue-scope', id)
    // Daily breakdown is Volume's alone; the suite pass rates card is By suite's alone.
    expect(within(panel).queryByRole('heading', { name: 'Daily breakdown' })).toBeNull()
    const suiteCard = within(panel).queryByRole('heading', { name: 'Suite pass rates — last 14 days' })
    if (id === 'by-suite') expect(suiteCard).toBeInTheDocument()
    else expect(suiteCard).toBeNull()
  })

  it('an unknown ?tab= reads as Volume', async () => {
    await renderAt('/trends?tab=nope')
    expect(within(screen.getByRole('tablist', { name: 'Trend views' })).getByRole('tab', { name: 'Volume' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('heading', { name: 'Daily breakdown' })).toBeInTheDocument()
  })

  it('a tab click writes ?tab=, and Volume clears it', async () => {
    await renderAt('/trends')
    openTab('Heatmap')
    expect(screen.getByTestId('location')).toHaveTextContent('/trends?tab=heatmap')
    expect(await screen.findByTestId('trends-catalogue')).toBeInTheDocument()
    openTab('Volume')
    expect(screen.getByTestId('location')).toHaveTextContent(/^\/trends$/)
    expect(screen.queryByTestId('trends-catalogue')).toBeNull()
  })

  // Each tab renders the catalogue with its own sections (`sections`): a
  // section another tab owns is not rendered, so it never asks (the e2e
  // proves the requests). No CSS hides anything any more.
  it.each([
    ['by-suite', ['trends-multi-series', 'trends-compare']],
    // EXEC-04b: the execution-time section above the p50 / p95 band.
    ['durations', ['trends-execution', 'trends-duration']],
    ['heatmap', ['trends-heatmap']],
  ] as const)('the %s tab renders the catalogue with exactly its sections', async (id, sections) => {
    await renderAt(`/trends?tab=${id}`)
    const scope = (await screen.findByTestId('trends-catalogue')).closest('[data-catalogue-scope]') as HTMLElement
    expect(catalogue.props[catalogue.props.length - 1].sections).toEqual(sections)
    // Composition, not concealment: the wrapper hides no section.
    expect(scope.className).not.toMatch(/:hidden/)
  })

  it('remounts the catalogue per tab: Durations then Heatmap are two mounts, never one hidden', async () => {
    await renderAt('/trends?tab=durations')
    const first = await screen.findByTestId('trends-catalogue')
    openTab('Heatmap')
    const second = await screen.findByTestId('trends-catalogue')
    expect(second).not.toBe(first)
    expect(second.closest('[data-catalogue-scope]')).toHaveAttribute('data-catalogue-scope', 'heatmap')
    expect(catalogue.props[catalogue.props.length - 1].sections).toEqual(['trends-heatmap'])
    expect(document.querySelectorAll('[data-testid="trends-catalogue"]')).toHaveLength(1)
  })
})

// TRND-05: the Pass rate tile printed the summary's RELATIVE change as "pp":
// a window that moved 80 % -> 88 % read "10pp vs prev window". It now prints
// `pass_rate_delta_pp` and names the window it compares against.
describe('TrendsPage TRND-05: the pass-rate delta is in percentage points', () => {
  beforeEach(async () => {
    analyticsControls.widgetIds = []
    frames.stacked.length = 0
    frames.timeSeries.length = 0
    const { useTimeWindowStore } = await import('@/store/timeWindowStore')
    useTimeWindowStore.setState({ days: 14 })
  })

  async function renderWithSummary(summary: Record<string, unknown>) {
    const { useTrendData, useDashboardSummary, useCoverage } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')
    const { shiftDayIso, utcDayIso } = await import('@/utils/calendarDay')
    const trend = [1, 2, 3].map((back) => ({
      date: shiftDayIso(utcDayIso(), -back), passed: 22, failed: 3, skipped: 0, broken: 0, total: 25, pass_rate: 88,
    }))
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({ data: { data: trend }, isLoading: false })
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({ data: summary })
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({ data: { summary: {}, suites: [] } })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    render(
      <MemoryRouter initialEntries={['/trends']}>
        <Routes><Route path="/trends" element={<TrendsPage />} /></Routes>
      </MemoryRouter>,
    )
    await screen.findByRole('heading', { name: 'Trends' })
  }

  const passRateTile = () =>
    within(screen.getByRole('region', { name: 'Trend metrics' })).getByText('Pass rate').closest('[data-metric-card]') as HTMLElement

  it('reads 80 % -> 88 % as 8 pp against the previous window of the picked length, not the relative 10', async () => {
    await renderWithSummary({ avg_pass_rate_7d: { value: 88, trend: 10, trend_direction: 'up', pass_rate_delta_pp: 8 } })
    fireEvent.click(windowOption('30d'))

    const change = passRateTile().querySelector('[data-metric-trend]') as HTMLElement
    expect(change).toHaveAttribute('data-metric-trend', 'up')
    expect(change).toHaveTextContent('Up 8 pp vs previous 30 days')
    expect(change).not.toHaveTextContent('10')
  })

  it('keeps a fractional move and states a drop as down', async () => {
    await renderWithSummary({ avg_pass_rate_7d: { value: 71.3, trend: -20.8, trend_direction: 'down', pass_rate_delta_pp: -18.75 } })

    const change = passRateTile().querySelector('[data-metric-trend]') as HTMLElement
    expect(change).toHaveAttribute('data-metric-trend', 'down')
    expect(change).toHaveTextContent('Down 18.8 pp vs previous 14 days')
  })

  it('shows no delta from a payload without points, never the relative trend as pp', async () => {
    await renderWithSummary({ avg_pass_rate_7d: { value: 88, trend: 10, trend_direction: 'up' } })

    const tile = passRateTile()
    expect(tile).not.toHaveTextContent(/pp vs/)
    expect(tile).toHaveTextContent('3 active days')
  })
})

// ── W08 "trends-coverage" ─────────────────────────────────────────────────
describe('TrendsPage W08', () => {
  function Location() {
    const location = useLocation()
    return <output data-testid="location">{`${location.pathname}${location.search}`}</output>
  }

  const SUITES = [
    { suite_name: 'Checkout', unique_tests: 6, passed: 40, failed: 10, skipped: 5, pass_rate: 80 },
    { suite_name: 'Payments', unique_tests: 4, passed: 21, failed: 21, skipped: 0, pass_rate: 50 },
  ]

  beforeEach(async () => {
    frames.stacked.length = 0
    frames.timeSeries.length = 0
    catalogue.props.length = 0
    catalogue.throws = false
    releaseList.items = []
    const { useTimeWindowStore } = await import('@/store/timeWindowStore')
    useTimeWindowStore.setState({ days: 14 })
    const { useProjectStore } = await import('@/store/projectStore')
    ;(useProjectStore as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      (selector: (state: { activeProjectId: string; activeProject: { name: string } | null }) => unknown) =>
        selector({ activeProjectId: 'proj-1', activeProject: { name: 'Project One' } }),
    )
    const views = await import('@/services/savedViewsService')
    ;(views.listSavedViews as ReturnType<typeof vi.fn>).mockResolvedValue([])
    ;(views.createSavedView as ReturnType<typeof vi.fn>).mockReset()
    ;(views.createSavedView as ReturnType<typeof vi.fn>).mockResolvedValue({})
  })

  async function allProjects() {
    const { useProjectStore } = await import('@/store/projectStore')
    ;(useProjectStore as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      (selector: (state: { activeProjectId: string; activeProject: null }) => unknown) =>
        selector({ activeProjectId: '__ALL__', activeProject: null }),
    )
  }

  async function dayBack(back: number) {
    const { shiftDayIso, utcDayIso } = await import('@/utils/calendarDay')
    return shiftDayIso(utcDayIso(), -back)
  }

  async function renderPage(path = '/trends', backs: number[] = [1, 2, 3, 4]) {
    const { useTrendData, useDashboardSummary, useCoverage } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')
    const trend = await Promise.all(
      backs.map(async (back) => ({ date: await dayBack(back), passed: 9, failed: 1, skipped: 0, broken: 0, total: 10, pass_rate: 90 })),
    )
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({ data: { data: trend }, isLoading: false })
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({ data: {} })
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({ data: { summary: {}, suites: SUITES } })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    render(
      // A fresh SWR cache per test: the saved-views list is read through SWR.
      <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
        <MemoryRouter initialEntries={[path]}>
          <Routes><Route path="/trends" element={<><TrendsPage /><Location /></>} /></Routes>
        </MemoryRouter>
      </SWRConfig>,
    )
    await screen.findByRole('heading', { name: 'Trends' })
  }

  const openMore = () => fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
  const scoreToggle = () => screen.getByRole('button', { name: /How this score is computed/ })

  // ── TRND-06: the server's summary ────────────────────────────────────────
  describe('TRND-06 the trend summary is the server’s', () => {
    async function summaryOf(over: Record<string, unknown>) {
      const { summaryFromTrend } = await import('@/test/trendSummaryFixture')
      return { ...summaryFromTrend([], 14), ...over }
    }

    it('the disclosure shows the server’s verdict, score and dimensions', async () => {
      trendSummary.override = await summaryOf({
        days_with_runs: 9,
        confidence: {
          score: 77,
          verdict: 'HEALTHY',
          band: 'High',
          dimensions: [
            { id: 'data_coverage', label: 'Data coverage', score: 64.29, weight: 0.4 },
            { id: 'sample_size', label: 'Sample size', score: 100, weight: 0.25 },
            { id: 'variance_stability', label: 'Variance stability', score: 80, weight: 0.2 },
            { id: 'tag_quality', label: 'Tag quality', score: 100, weight: 0.15 },
          ],
        },
      })
      await renderPage()
      expect(scoreToggle()).toHaveTextContent('Trend healthy · confidence 77 / 100')
      openScore()
      expect(screen.getByRole('meter', { name: 'Trend confidence' })).toHaveAttribute('aria-valuetext', '77 of 100, High')
      const verdict = screen.getByRole('region', { name: 'Trend verdict' })
      expect(verdict).toHaveTextContent('9 active days · pass rate steady')
      expect(within(verdict).getByText('Data coverage').closest('[data-metric-card]')).toHaveTextContent('64')
      // The page asked for its window and scope.
      expect(trendSummary.calls[trendSummary.calls.length - 1]).toEqual([14, null])
    })

    it.each<[string, unknown]>([
      ['garbage', { confidence: 'x' }],
      ['null', null],
      ['an empty object', {}],
    ])('a %s summary reads "Score not available": no callout, the KPI strip and the charts intact', async (_name, raw) => {
      trendSummary.override = raw
      // A trailing 10-day silence in the trend: without the server's gap there is no callout.
      await renderPage('/trends', [10, 11, 12, 13])
      expect(scoreToggle()).toHaveTextContent('Score not available')
      openScore()
      expect(screen.queryByRole('meter', { name: 'Trend confidence' })).toBeNull()
      expect(document.querySelector('[data-score-unavailable]')).toHaveTextContent('Score not available')
      expect(screen.queryByRole('heading', { name: 'Schedule appears paused' })).toBeNull()
      expect(document.querySelector('[data-cadence-gap]')).toBeNull()
      expect(screen.getByRole('region', { name: 'Trend metrics' })).toHaveTextContent('Pass rate')
      expect(screen.getByRole('heading', { name: 'Pass rate trend' })).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: 'Daily breakdown' })).toBeInTheDocument()
    })

    it('a failed summary read is "Score not available" too', async () => {
      trendSummary.override = null
      trendSummary.error = new Error('503')
      await renderPage()
      expect(scoreToggle()).toHaveTextContent('Score not available')
    })

    it('the callout, its Silent days and its dates are the server’s gap', async () => {
      const { utcDayIso, formatDayIso } = await import('@/utils/calendarDay')
      const start = await dayBack(8)
      trendSummary.override = await summaryOf({
        days_with_runs: 5,
        gap: { silent_days: 9, start, end: utcDayIso(), ongoing: true, expected_runs_missed: 9 },
        confidence: { score: 40, verdict: 'MIXED', band: 'Moderate', dimensions: [] },
      })
      // The trend itself has no gap at all: what is drawn is the server's.
      await renderPage('/trends', [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13])
      const callout = screen.getByRole('heading', { name: 'Schedule appears paused' }).closest('section') as HTMLElement
      expect(within(callout).getByText('Silent days').parentElement).toHaveTextContent('9')
      expect(within(callout).getByText('Expected runs missed').parentElement).toHaveTextContent('~9')
      expect(callout).toHaveTextContent(`No executions have landed since ${formatDayIso(start)}.`)
      expect(document.querySelector('[data-cadence-gap]')).toHaveTextContent('9-day silence')
    })

    it('W29b: the cadence card is a Panel with an h3; the callout stats and the dimensions are sm tiles, no sheen', async () => {
      const { utcDayIso } = await import('@/utils/calendarDay')
      trendSummary.override = await summaryOf({
        days_with_runs: 5,
        gap: { silent_days: 9, start: await dayBack(8), end: utcDayIso(), ongoing: true, expected_runs_missed: 9 },
        confidence: {
          score: 40,
          verdict: 'MIXED',
          band: 'Moderate',
          dimensions: [
            { id: 'data_coverage', label: 'Data coverage', score: 30, weight: 0.4 },
            { id: 'sample_size', label: 'Sample size', score: 90, weight: 0.6 },
          ],
        },
      })
      await renderPage('/trends', [0, 1, 2, 3, 4])
      const cadence = screen.getByRole('heading', { level: 3, name: /^Run cadence — last/ })
      expect(cadence.closest('[data-panel]')).toHaveClass('card')
      expect(cadence).toHaveAttribute('title', expect.stringMatching(/^Each cell is one day/))
      // The callout's two stats: in-panel tiles in the danger ink, never a page-made 20 px number.
      const callout = screen.getByRole('heading', { name: 'Schedule appears paused' }).closest('section') as HTMLElement
      const stats = [...callout.querySelectorAll('[data-metric-card="stat"]')]
      expect(stats.map((t) => t.querySelector('p')?.textContent)).toEqual(['Silent days', 'Expected runs missed'])
      for (const stat of stats) expect(stat.querySelector('p.tabular-nums')).toHaveClass('text-[var(--ui-danger-ink)]')
      // The two dimensions: sm tiles with their weight and bar, on the sunken well.
      openScore()
      const verdict = screen.getByRole('region', { name: 'Trend verdict' })
      const tiles = [...verdict.querySelectorAll('[data-metric-card="stat"]')]
      expect(tiles.map((t) => t.querySelector('p')?.textContent)).toEqual(['Data coverage40%', 'Sample size60%'])
      expect(tiles.map((t) => t.querySelector('p.tabular-nums')?.className.match(/text-\[var\(--ui-\w+-ink\)\]/)?.[0]))
        .toEqual(['text-[var(--ui-danger-ink)]', 'text-[var(--ui-success-ink)]'])
      for (const tile of tiles) expect(tile.querySelector('[data-metric-bar]')).not.toBeNull()
      expect(verdict.innerHTML).not.toMatch(/rgba\(/)
    })

    it('a past gap (not ongoing) is "Run gap in this window"', async () => {
      trendSummary.override = await summaryOf({
        gap: { silent_days: 7, start: await dayBack(12), end: await dayBack(6), ongoing: false, expected_runs_missed: 7 },
        confidence: { score: 40, verdict: 'MIXED', band: 'Moderate', dimensions: [] },
      })
      await renderPage()
      expect(screen.getByRole('heading', { name: 'Run gap in this window' })).toBeInTheDocument()
      expect(screen.queryByRole('heading', { name: 'Schedule appears paused' })).toBeNull()
    })
  })

  describe('TRND-06 the Suite pass rates sparklines', () => {
    it('draws a row’s line from the shared request; a suite outside the 7 busiest reads "—" with the reason', async () => {
      sparklines.lines = { checkout: [80, null, 90, 85] }
      await renderPage('/trends?tab=by-suite')
      const card = screen.getByRole('heading', { name: 'Suite pass rates — last 14 days' }).closest('div.card') as HTMLElement
      const checkout = within(card).getByRole('link', { name: 'Checkout' }).closest('div.grid') as HTMLElement
      // The sparkline is a lazy module (section-only request code stays out of the page chunk).
      await waitFor(() => expect(checkout.querySelector('[data-sparkline]')).not.toBeNull())
      const line = checkout.querySelector('[data-sparkline]') as HTMLElement
      expect(line.querySelector('path[data-part="line"]')).not.toBeNull()
      expect(within(line).getByRole('img')).toHaveAccessibleName(/^Checkout pass rate per day: 3 points/)
      const payments = within(card).getByRole('link', { name: 'Payments' }).closest('div.grid') as HTMLElement
      expect(payments.querySelector('[data-sparkline]')).toBeNull()
      expect(payments.querySelector('[data-sparkline-missing]')).toHaveAttribute('title', NOT_AMONG_BUSIEST)
      expect(NOT_AMONG_BUSIEST).toBe('Not among the 7 busiest suites drawn in "Pass rate by suite"')
      // The card asks once it is near, in the page's window and suite scope.
      expect(sparklines.calls[sparklines.calls.length - 1]).toEqual([14, null, true])
    })

    it('an unavailable series is "—" on every row, and the card still renders', async () => {
      sparklines.state = 'unavailable'
      await renderPage('/trends?tab=by-suite')
      const card = screen.getByRole('heading', { name: 'Suite pass rates — last 14 days' }).closest('div.card') as HTMLElement
      await waitFor(() => expect(card.querySelectorAll('[data-sparkline-missing]')).toHaveLength(2))
      expect(card.querySelector('[data-sparkline]')).toBeNull()
      expect(card).toHaveTextContent('80%')
    })

    it('asks nothing on the Volume tab', async () => {
      await renderPage('/trends')
      expect(sparklines.calls).toHaveLength(0)
    })
  })

  // ── TRND-02: Compare runs ────────────────────────────────────────────────
  describe('TRND-02 Compare runs', () => {
    it('the ⋯ item opens the latest two runs of the selected suite, else the manual form', async () => {
      await renderPage()
      openMore()
      expect(screen.getByRole('menuitem', { name: 'Compare runs' })).toHaveAttribute('href', '/runs/compare')
      openMore()
      fireEvent.change(screen.getByDisplayValue('All suites'), { target: { value: 'Checkout' } })
      openMore()
      expect(screen.getByRole('menuitem', { name: 'Compare runs' })).toHaveAttribute('href', '/runs/compare?mode=latest&suite=Checkout')
    })

    it('each Suite pass rates row links to the latest two runs of its suite', async () => {
      await renderPage('/trends?tab=by-suite')
      expect(screen.getByRole('link', { name: 'Compare the latest two runs of Checkout' })).toHaveAttribute(
        'href',
        '/runs/compare?mode=latest&suite=Checkout',
      )
      expect(screen.getByRole('link', { name: 'Compare the latest two runs of Payments' })).toHaveAttribute('title', 'Compare the latest two runs of Payments')
    })

    it('All Projects: "Compare runs (pick a project)" is disabled and no row carries the icon', async () => {
      await allProjects()
      await renderPage('/trends?tab=by-suite')
      openMore()
      expect(screen.getByRole('menuitem', { name: 'Compare runs (pick a project)' })).toBeDisabled()
      expect(screen.queryByRole('link', { name: /Compare the latest two runs/ })).toBeNull()
    })
  })

  // ── TRND-01: Export PDF and Email report ────────────────────────────────
  describe('TRND-01 Export PDF and Email report', () => {
    it('a QA lead sees Export PDF and Email report…; Email report opens the dialog with the page scope', async () => {
      role.isQaLead = true
      role.isQaEngineer = true
      await renderPage()
      openMore()
      expect(screen.getByRole('menuitem', { name: 'Export PDF' })).toBeEnabled()
      fireEvent.click(screen.getByRole('menuitem', { name: 'Email report…' }))
      expect(screen.getByTestId('email-report-dialog')).toBeInTheDocument()
      expect(dialogs.email[dialogs.email.length - 1]).toMatchObject({ projectId: 'proj-1', days: 14, suiteFilter: null, release: null, suiteLabel: null })
    })

    it('a viewer sees Export PDF only', async () => {
      await renderPage()
      openMore()
      expect(screen.getByRole('menuitem', { name: 'Export PDF' })).toBeInTheDocument()
      expect(screen.queryByRole('menuitem', { name: /Email report/ })).toBeNull()
    })

    it('Export PDF downloads the page’s window and suite as trends-{project}-{days}d.pdf', async () => {
      const blob = new Blob(['%PDF-1.4'], { type: 'application/pdf' })
      reportService.downloadTrendsPdf.mockResolvedValue(blob)
      await renderPage()
      fireEvent.change(screen.getByDisplayValue('All suites'), { target: { value: 'Checkout' } })
      openMore()
      fireEvent.click(screen.getByRole('menuitem', { name: 'Export PDF' }))
      await waitFor(() => expect(download.downloadBlob).toHaveBeenCalledTimes(1))
      expect(reportService.downloadTrendsPdf).toHaveBeenCalledWith({ projectId: 'proj-1', days: 14, suite: 'Checkout', release: null })
      expect(download.downloadBlob).toHaveBeenCalledWith(blob, 'trends-project_one-14d.pdf')
    })

    it('All Projects: "Export PDF (pick a project)" is disabled and asks nothing', async () => {
      role.isQaLead = true
      await allProjects()
      await renderPage()
      openMore()
      expect(screen.getByRole('menuitem', { name: 'Export PDF (pick a project)' })).toBeDisabled()
      expect(screen.getByRole('menuitem', { name: 'Email report… (pick a project)' })).toBeDisabled()
      expect(reportService.downloadTrendsPdf).not.toHaveBeenCalled()
    })
  })

  // ── TRND-04: notes on the timeline ──────────────────────────────────────
  describe('TRND-04 timeline notes', () => {
    const note = (over: Record<string, unknown>) => ({
      id: 'n1',
      project_id: 'proj-1',
      starts_on: '2026-09-10',
      ends_on: '2026-09-12',
      text: 'release freeze',
      suite_name: null,
      created_by_name: 'Ada',
      can_edit: false,
      created_at: null,
      updated_at: null,
      ...over,
    })

    it('asks the window’s notes (its first and last UTC day; the suite only when one is selected)', async () => {
      await renderPage()
      expect(timeline.calls[timeline.calls.length - 1]).toEqual([{ from: await dayBack(13), to: await dayBack(0) }, null])
      fireEvent.change(screen.getByDisplayValue('All suites'), { target: { value: 'Checkout' } })
      expect(timeline.calls[timeline.calls.length - 1]).toEqual([{ from: await dayBack(13), to: await dayBack(0) }, 'Checkout'])
    })

    it('draws the window’s notes on the hero, with the Notes (n) chip; a suite note only under its suite', async () => {
      const from = await dayBack(5)
      const to = await dayBack(3)
      timeline.notes = [
        note({ id: 'freeze', starts_on: from, ends_on: to }),
        note({ id: 'checkout-only', starts_on: to, ends_on: to, text: 'checkout migration', suite_name: 'Checkout' }),
      ]
      await renderPage()
      expect(lastTimeSeries().model.notes.map((n) => [n.id, n.kind, n.from, n.to])).toEqual([['freeze', 'band', from, to]])
      expect(screen.getByRole('button', { name: 'Notes (1)' })).toBeInTheDocument()
      fireEvent.change(screen.getByDisplayValue('All suites'), { target: { value: 'Checkout' } })
      expect(lastTimeSeries().model.notes.map((n) => n.id)).toEqual(['freeze', 'checkout-only'])
      expect(screen.getByRole('button', { name: 'Notes (2)' })).toBeInTheDocument()
    })

    it('a malformed notes answer draws no note and no error', async () => {
      timeline.notes = 'garbage'
      await renderPage()
      expect(lastTimeSeries().model.notes).toEqual([])
      expect(screen.queryByRole('button', { name: /^Notes \(/ })).toBeNull()
      expect(screen.getByRole('heading', { name: 'Pass rate trend' })).toBeInTheDocument()
    })

    it('a QA engineer pins a note from the cadence card, a dragged range, or the gap callout', async () => {
      role.isQaEngineer = true
      await renderPage('/trends', [10, 11, 12, 13])
      const { utcDayIso } = await import('@/utils/calendarDay')
      fireEvent.click(screen.getByRole('button', { name: 'Pin a note' }))
      expect(dialogs.pin[dialogs.pin.length - 1]).toMatchObject({ range: { from: utcDayIso(), to: utcDayIso() }, note: null, suite: null })
      // A mouse range on the strip: those days.
      const cells = [...document.querySelectorAll<HTMLElement>('[data-day-cell]')]
      fireEvent.mouseDown(cells[3], { button: 0 })
      fireEvent.mouseEnter(cells[5])
      fireEvent.mouseUp(cells[5])
      expect(dialogs.pin[dialogs.pin.length - 1]).toMatchObject({ range: { from: await dayBack(10), to: await dayBack(8) } })
      // The gap callout: the server's gap dates.
      fireEvent.click(screen.getByRole('button', { name: 'Pin a note for these days' }))
      expect(dialogs.pin[dialogs.pin.length - 1]).toMatchObject({ range: { from: await dayBack(9), to: await dayBack(0) } })
    })

    it('a viewer sees every note and none of the controls', async () => {
      timeline.notes = [note({ starts_on: await dayBack(4), ends_on: await dayBack(4) })]
      await renderPage('/trends', [10, 11, 12, 13])
      expect(screen.queryByRole('button', { name: /Pin a note/ })).toBeNull()
      expect(lastTimeSeries().model.notes).toHaveLength(1)
      // Dragging the strip pins nothing.
      const cells = [...document.querySelectorAll<HTMLElement>('[data-day-cell]')]
      fireEvent.mouseDown(cells[3], { button: 0 })
      fireEvent.mouseUp(cells[3])
      expect(dialogs.pin).toHaveLength(0)
    })

    it('All Projects: no notes asked, none drawn, no controls', async () => {
      role.isQaEngineer = true
      timeline.notes = [note({ starts_on: await dayBack(4), ends_on: await dayBack(4) })]
      await allProjects()
      await renderPage()
      expect(timeline.calls.every(([range]) => range === null)).toBe(true)
      expect(screen.queryByRole('button', { name: /Pin a note/ })).toBeNull()
    })
  })

  // ── TRND-10: customised charts in saved views ───────────────────────────
  describe('TRND-10 saved views carry the customised charts', () => {
    async function saveView(name: string) {
      fireEvent.click(document.querySelector('[data-saved-views-trigger]') as HTMLElement)
      fireEvent.change(await screen.findByPlaceholderText('e.g. Payments release watch'), { target: { value: name } })
      fireEvent.click(screen.getByRole('button', { name: 'Save view' }))
      const views = await import('@/services/savedViewsService')
      await waitFor(() => expect(views.createSavedView).toHaveBeenCalledTimes(1))
      return (views.createSavedView as ReturnType<typeof vi.fn>).mock.calls[0][0] as { filters: Record<string, unknown> }
    }

    it('nothing customised: the saved filters are exactly today’s, with no charts key', async () => {
      await renderPage()
      const body = await saveView('Plain')
      expect(body.filters).not.toHaveProperty('charts')
      expect(body.filters).toMatchObject({ kind: 'report_view', page: 'trends', window: 14 })
    })

    it('a customised Compare is saved under filters.charts["trends.compare"]', async () => {
      await renderPage()
      act(() => chartConfigStore.set('trends.compare', { version: 1, by: null, metric: 'failures', suites: ['checkout', 'search'] }))
      const body = await saveView('Failures')
      expect(body.filters.charts).toEqual({ 'trends.compare': { version: 1, by: null, metric: 'failures', suites: ['checkout', 'search'] } })
    })

    it('this browser’s stored customisation is saved even before its chart mounts', async () => {
      window.localStorage.setItem(
        'testlookup.chartConfig.trends.suite-series',
        JSON.stringify({ version: 1, metric: 'executions', seriesBy: 'suite', bucket: 'day', topN: 5 }),
      )
      await renderPage()
      const body = await saveView('Mine')
      expect(body.filters.charts).toEqual({ 'trends.suite-series': { version: 1, metric: 'executions', seriesBy: 'suite', bucket: 'day', topN: 5 } })
    })

    it('opening a view applies its charts to the store and writes Compare’s chosen suites into the URL', async () => {
      const views = await import('@/services/savedViewsService')
      ;(views.listSavedViews as ReturnType<typeof vi.fn>).mockResolvedValue([
        {
          id: 'v1',
          user_id: 'u2',
          project_id: 'proj-1',
          name: 'Teammate compare',
          description: null,
          page: 'trends',
          filters: {
            kind: 'report_view',
            page: 'trends',
            window: 14,
            charts: { 'trends.compare': { version: 1, by: 'suite', metric: 'failures', suites: ['billing', 'checkout'] } },
          },
          is_shared: true,
          is_default: false,
          created_at: '2026-10-01T00:00:00Z',
          updated_at: null,
        },
      ])
      chartConfigStore.set('trends.suite-series', { version: 1, metric: 'failures', seriesBy: 'suite', bucket: 'day', topN: 3 })
      await renderPage('/trends?tab=by-suite')
      fireEvent.click(document.querySelector('[data-saved-views-trigger]') as HTMLElement)
      fireEvent.click(await screen.findByRole('button', { name: /Teammate compare/ }))
      expect(chartConfigStore.get('trends.compare')).toEqual({ version: 1, by: 'suite', metric: 'failures', suites: ['billing', 'checkout'] })
      // A view is the whole configuration: the suite series it does not carry goes back to its default.
      expect(chartConfigStore.get('trends.suite-series')).toBeNull()
      expect(useChartConfigStore.getState().viewToken).toBe(1)
      await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('cmp_suite=billing&cmp_suite=checkout'))
      expect(screen.getByTestId('location')).toHaveTextContent('tab=by-suite')
    })
  })
})
