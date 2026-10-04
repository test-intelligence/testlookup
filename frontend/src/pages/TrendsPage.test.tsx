import { act, fireEvent, render, screen, within } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type StackedColumnChartFrame from '@/components/charts/StackedColumnChartFrame'
import type TimeSeriesChartFrame from '@/components/charts/TimeSeriesChartFrame'
import { utcDayLabel } from '@/components/charts/stackedColumnModel'
import TrendsPage from './TrendsPage'

// The two kit frames render for real; these spies only record what the page
// handed them, so a test can read the page's adapter output (statuses, gaps,
// the target) rather than infer it from a zero-size jsdom plot.
const frames = vi.hoisted(() => ({
  stacked: [] as unknown[],
  timeSeries: [] as unknown[],
  timeSeriesMounts: 0,
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
  const { useEffect } = await import('react')
  const Real = actual.default
  function SpiedTimeSeriesChartFrame(props: ComponentProps<typeof TimeSeriesChartFrame>) {
    frames.timeSeries.push(props)
    // One count per MOUNT, so a test can tell a re-mount from a re-render.
    useEffect(() => {
      frames.timeSeriesMounts += 1
    }, [])
    return <Real {...props} />
  }
  return { ...actual, default: SpiedTimeSeriesChartFrame }
})

// Wave 2.6 (VIZ-408): the one seam, the release list the pass-rate markers
// read, and the lazy catalogue chunk, each controlled here. The catalogue is
// a stub that records its props: its own sections are TrendsCatalogue.test.tsx's.
// `pending`: the status lookup has not answered yet (`useCatalogueRolloutStatus` is undefined).
const rollout = vi.hoisted(() => ({ on: false, pending: false }))
vi.mock('@/components/reports/catalogue/useCatalogueRollout', () => ({
  useCatalogueRollout: () => !rollout.pending && rollout.on,
  useCatalogueRolloutStatus: () => (rollout.pending ? undefined : rollout.on),
}))

const releaseList = vi.hoisted(() => ({ items: [] as { id: string; name: string; released_at: string | null; planned_date: string | null }[] }))
vi.mock('@/hooks/useReleases', () => ({
  useReleases: () => ({ data: { items: releaseList.items } }),
}))

// `throws`: the section's top level throws (or its chunk failed to load).
const catalogue = vi.hoisted(() => ({ props: [] as { days: number; suiteFilter: unknown }[], throws: false }))
vi.mock('@/components/reports/catalogue/TrendsCatalogue', () => ({
  default: (props: { days: number; suiteFilter: unknown }) => {
    catalogue.props.push(props)
    if (catalogue.throws) throw new Error('Failed to fetch dynamically imported module')
    return <div data-testid="trends-catalogue" />
  },
}))

vi.mock('@/hooks/useMetrics', () => ({
  useTrendData: vi.fn(),
  useDashboardSummary: vi.fn(),
  useCoverage: vi.fn(),
  useFlakyTests: vi.fn(),
}))

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
}))

// One suite to pick, so a test can apply a filter; nothing else reads it.
vi.mock('@/hooks/useSuiteOptions', () => ({
  useSuiteOptions: vi.fn(() => ({ options: ['Checkout'], isLoading: false })),
}))

const analyticsControls = vi.hoisted(() => ({
  widgetIds: ['trends_kpis', 'daily_breakdown', 'pass_rate_trend'],
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

/** The run-cadence strip's sr-only table, one [day, state, marker] per cell. */
function cadenceRows(): string[][] {
  const table = screen.getByRole('table', { name: /^Run cadence:/ })
  return [...(table as HTMLTableElement).tBodies[0].rows].map((row) => [...row.cells].map((cell) => cell.textContent ?? ''))
}

describe('TrendsPage', () => {
  beforeEach(() => {
    analyticsControls.widgetIds = ['trends_kpis', 'daily_breakdown', 'pass_rate_trend']
    frames.stacked.length = 0
    frames.timeSeries.length = 0
  })

  it('renders the trend workflow strip above the charts', async () => {
    const { useTrendData, useDashboardSummary, useCoverage, useFlakyTests } = await import('@/hooks/useMetrics')
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
    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })

    render(
      <MemoryRouter initialEntries={['/trends']}>
        <Routes>
          <Route path="/trends" element={<TrendsPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Trends workflow/i)).toBeInTheDocument()
    expect(screen.getByText(/Trend capture/i)).toBeInTheDocument()
    expect(screen.getAllByText(/Trends/i).length).toBeGreaterThan(0)
  })

  it('removes deselected trend panels from the rendered layout', async () => {
    const { useTrendData, useDashboardSummary, useCoverage, useFlakyTests } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')
    analyticsControls.widgetIds = []
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [{ date: '2026-04-01', passed: 4, failed: 1, skipped: 0, broken: 0, pass_rate: 80 }] },
      isLoading: false,
    })
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({ data: {} })
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({ data: { summary: {}, suites: [] } })
    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })

    render(
      <MemoryRouter initialEntries={['/trends']}>
        <Routes><Route path="/trends" element={<TrendsPage />} /></Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: 'Trends' })).toBeInTheDocument()
    expect(screen.queryByText('Daily breakdown')).not.toBeInTheDocument()
    expect(screen.queryByText('Pass rate trend')).not.toBeInTheDocument()
  })

  // Regression: the headline pass rate used to divide by every execution,
  // skips included — 31/60 = 51.7% — while the same payload's `pass_rate`
  // field and /overview both said 57.4% (31 evaluated-of-54, skips excluded).
  // Two figures for one window on two pages. The denominator is now the
  // backend's: passed + failed + broken.
  it('excludes skips from the headline pass rate, matching the API and /overview', async () => {
    const { useTrendData, useDashboardSummary, useCoverage, useFlakyTests } = await import('@/hooks/useMetrics')
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
    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })
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
    // The narrative rounds, so the old 51.7% surfaced there as "52%".
    expect(screen.queryByText(/headline 52% pass rate/)).not.toBeInTheDocument()
    expect(screen.getByText(/headline 57% pass rate/)).toBeInTheDocument()
  })

  // Regression: test executions were labelled "runs" — a 6-run window read
  // "31 / 60 runs". Same wording defect #643 fixed on the dashboard.
  it('calls test executions executions, not runs', async () => {
    const { useTrendData, useDashboardSummary, useCoverage, useFlakyTests } = await import('@/hooks/useMetrics')
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
    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })

    render(
      <MemoryRouter initialEntries={['/trends']}>
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
    const { useTrendData, useDashboardSummary, useCoverage, useFlakyTests } = await import('@/hooks/useMetrics')
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
    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })

    render(
      <MemoryRouter initialEntries={['/trends']}>
        <Routes>
          <Route path="/trends" element={<TrendsPage />} />
        </Routes>
      </MemoryRouter>,
    )

    await screen.findByText(/Trends workflow/i)
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
    analyticsControls.widgetIds = ['trends_kpis', 'daily_breakdown', 'pass_rate_trend']
    frames.stacked.length = 0
    frames.timeSeries.length = 0
  })

  async function renderWindow(trend: unknown[]) {
    const { useTrendData, useDashboardSummary, useCoverage, useFlakyTests } = await import('@/hooks/useMetrics')
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
    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })
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
      const cell = within(kpis).getByText(label).closest('div.flex.flex-col') as HTMLElement
      // The label's lucide icon is the only svg left in the cell.
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
    const meter = screen.getByRole('meter', { name: 'Trend confidence' })
    expect(meter).toHaveAttribute('aria-valuemin', '0')
    expect(meter).toHaveAttribute('aria-valuemax', '100')
    expect(meter.getAttribute('aria-valuetext')).toMatch(/^\d+ of 100, (Low|Moderate|High)$/)
  })

  it('shows a PENDING window as not measured on the meter, never a 0 score', async () => {
    await renderWindow([])
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

  it('has no per-suite micro-bar: a suite row is the name and its rate', async () => {
    const { trend } = await windowFixture()
    await renderWindow(trend)
    const suites = screen.getByRole('heading', { name: 'Suite pass rates · today' }).closest('div.rounded-xl') as HTMLElement
    const row = within(suites).getByText('Payments', { exact: true }).parentElement as HTMLElement
    expect(row).toHaveTextContent('50%')
    expect(row.querySelectorAll('i')).toHaveLength(0)
    expect(row.children).toHaveLength(2)
  })
})

// Wave 2.6 (VIZ-408 + VIZ-106): the catalogue seam, the one-request window,
// and the page's own responsive rework.
describe('TrendsPage rollout (Wave 2.6)', () => {
  beforeEach(async () => {
    analyticsControls.widgetIds = ['trends_kpis', 'daily_breakdown', 'pass_rate_trend']
    frames.stacked.length = 0
    frames.timeSeries.length = 0
    frames.timeSeriesMounts = 0
    catalogue.props.length = 0
    catalogue.throws = false
    rollout.on = false
    rollout.pending = false
    releaseList.items = []
    const { useTimeWindowStore } = await import('@/store/timeWindowStore')
    // The app-wide default: what a reader arriving from any other page holds.
    useTimeWindowStore.setState({ days: 30 })
    const metrics = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')
    const { useSuiteOptions } = await import('@/hooks/useSuiteOptions')
    for (const hook of [metrics.useTrendData, metrics.useDashboardSummary, metrics.useCoverage, metrics.useFlakyTests, useRuns, useSuiteOptions]) {
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
    const { useTrendData, useDashboardSummary, useCoverage, useFlakyTests } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({ data: { data: trend }, isLoading: false })
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({ data: {} })
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({ data: { summary: {}, suites: [] } })
    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })
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
    for (const hook of [metrics.useTrendData, metrics.useDashboardSummary, metrics.useCoverage, metrics.useFlakyTests, useSuiteOptions]) {
      const calls = (hook as ReturnType<typeof vi.fn>).mock.calls
      expect(calls.length).toBeGreaterThan(0)
      expect(new Set(calls.map((call) => call[0]))).toEqual(new Set([14]))
    }
    const runCalls = (useRuns as ReturnType<typeof vi.fn>).mock.calls
    expect(runCalls.length).toBeGreaterThan(0)
    expect(new Set(runCalls.map((call) => (call[0] as { days: number }).days))).toEqual(new Set([14]))
    // The shared preference is still reset, so other pages follow.
    expect(useTimeWindowStore.getState().days).toBe(14)
    expect(screen.getByRole('tab', { name: '14d' })).toHaveAttribute('aria-selected', 'true')
  })

  it('still follows the window picker after the reset', async () => {
    await renderTrends(await activeDays(3))
    const { useTrendData } = await import('@/hooks/useMetrics')
    fireEvent.click(screen.getByRole('tab', { name: '90d' }))
    const calls = (useTrendData as ReturnType<typeof vi.fn>).mock.calls
    expect(calls[calls.length - 1][0]).toBe(90)
    expect(screen.getByRole('tab', { name: '90d' })).toHaveAttribute('aria-selected', 'true')
  })

  it('with the flag off: no catalogue, no section, and the pass-rate frame exactly as in Wave 2.5', async () => {
    releaseList.items = [{ id: 'r1', name: 'R1', released_at: await dayBack(2), planned_date: null }]
    await renderTrends(await activeDays(10))
    await screen.findByRole('heading', { name: 'Pass rate trend' })
    expect(screen.queryByTestId('trends-catalogue')).toBeNull()
    expect(catalogue.props).toHaveLength(0)
    expect(document.querySelector('[data-catalogue-section]')).toBeNull()
    expect(document.querySelector('[data-trend-controls]')).toBeNull()
    const props = frames.timeSeries.map((p) => p as TimeSeriesProps)
    expect(props.length).toBeGreaterThan(0)
    for (const p of props) {
      expect(p.trendAnalysis).toBeUndefined()
      expect(p.zoom).toBeUndefined()
      // No release markers: the flag-off page never reads the release list.
      expect(p.model?.markers ?? []).toEqual([])
    }
    expect(Object.keys(lastTimeSeries()).sort()).toEqual(
      ['headingLevel', 'height', 'model', 'rateTarget', 'state', 'takeaway', 'title'].sort(),
    )
  })

  it('with the flag on: the pass-rate frame offers the trend overlays, the brush and the release markers', async () => {
    rollout.on = true
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
    rollout.on = true
    await renderTrends(await activeDays(4))
    fireEvent.click(screen.getByRole('tab', { name: '7d' }))
    const section = document.querySelector('[data-catalogue-section="trends-pass-rate"]') as HTMLElement
    const toggles = [...section.querySelectorAll('[data-trend-toggle]')]
    expect(toggles.length).toBeGreaterThan(0)
    for (const toggle of toggles) expect(toggle).toHaveAttribute('aria-disabled', 'true')
    expect(section.querySelector('[data-trend-disabled-reason]')).toHaveTextContent('Needs at least 7 days with runs')
    await screen.findByTestId('trends-catalogue')
    expect(lastCatalogue().days).toBe(7)
  })

  it('with the flag on, mounts the catalogue with the page window and suite scope', async () => {
    rollout.on = true
    await renderTrends(await activeDays(3))
    await screen.findByTestId('trends-catalogue')
    expect(lastCatalogue()).toEqual({ days: 14, suiteFilter: null })
    fireEvent.change(screen.getByDisplayValue('All suites'), { target: { value: 'Checkout' } })
    expect(lastCatalogue()).toEqual({ days: 14, suiteFilter: 'Checkout' })
    // Below the body grid, above the provenance line.
    const grid = document.querySelector('.trends-body-grid') as HTMLElement
    const stub = screen.getByTestId('trends-catalogue')
    expect(grid.compareDocumentPosition(stub) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(stub.compareDocumentPosition(screen.getByText('Provenance')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  // R1-1: the section chunk failing to load, or the section throwing, is the
  // section's own error state. Before, the nearest boundary was the route's,
  // so the whole page became "Something went wrong loading this page".
  it('with the flag on, a section that throws leaves the page and shows the section error', async () => {
    rollout.on = true
    catalogue.throws = true
    const spy = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    try {
      await renderTrends(await activeDays(3))
      expect(await screen.findByText('Failed to load charts')).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: 'Trends' })).toBeInTheDocument()
      expect(screen.getByRole('heading', { name: 'Pass rate trend' })).toBeInTheDocument()
      expect(screen.getByText('Provenance')).toBeInTheDocument()
    } finally {
      spy.mockRestore()
    }
  })

  // R2-4: at 375 px the three steps shared one row: the ordinals were drawn
  // over the titles, "comparison" ran into the next check icon and the counts
  // were cut to "1 e...". At 640 a third of the row still cut every count
  // (measured). One step per row below lg; the three columns, each but the
  // last with its divider, from lg up (the desktop shell: unchanged).
  it('the workflow ribbon stacks its steps below lg and keeps three columns from lg up', async () => {
    await renderTrends(await activeDays(3))
    const ribbon = screen.getByRole('region', { name: 'Trends workflow' })
    const steps = within(ribbon).getAllByRole('button', { name: /^Stage \d/ })
    expect(steps).toHaveLength(3)
    const grid = steps[0].parentElement as HTMLElement
    const classes = grid.className.split(/\s+/)
    expect(classes).toEqual(expect.arrayContaining(['grid', 'grid-cols-1', 'lg:grid-cols-3']))
    // No column rule that applies below lg.
    expect(grid.style.gridTemplateColumns).toBe('')
    expect(classes.filter((c) => /^grid-cols-/.test(c))).toEqual(['grid-cols-1'])
    // The dividers are columns' dividers: from lg up only, never on the last step.
    steps.forEach((step, i) => {
      expect(step.style.borderRight).toBe('')
      const stepClasses = step.className.split(/\s+/)
      expect(stepClasses.includes('lg:border-r')).toBe(i < steps.length - 1)
      expect(stepClasses.some((c) => c === 'border-r' || c === 'border')).toBe(false)
    })
  })

  // The catalogue gets the window the page SHOWS (snapped to its options),
  // never the raw shared preference: 45 days picked elsewhere reads as 30
  // here, and the charts must say 30 like every other number on the page.
  it('hands the catalogue the snapped page window, not the raw stored one', async () => {
    rollout.on = true
    await renderTrends(await activeDays(3))
    await screen.findByTestId('trends-catalogue')
    const { useTimeWindowStore } = await import('@/store/timeWindowStore')
    act(() => useTimeWindowStore.getState().setDays(45))
    expect(screen.getByRole('tab', { name: '30d' })).toHaveAttribute('aria-selected', 'true')
    expect(lastCatalogue().days).toBe(30)
  })

  it('is one column below 1024 px: the body grid and the verdict card are fluid, with no fixed columns', async () => {
    await renderTrends(await activeDays(3))
    const grid = document.querySelector('.trends-body-grid') as HTMLElement
    expect(grid).toHaveClass('grid-cols-1', 'lg:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)]')
    expect(grid.style.gridTemplateColumns).toBe('')
    const verdict = screen.getByRole('region', { name: 'Trend verdict' })
    expect(verdict).toHaveClass('grid-cols-1', 'lg:grid-cols-[1.45fr_1fr]')
    expect(verdict.style.gridTemplateColumns).toBe('')
  })

  it('no longer apologises for narrow screens', async () => {
    await renderTrends(await activeDays(3))
    expect(screen.queryByText(/Wider screen needed/)).toBeNull()
  })

  // The two pass-rate cards differ in height (the flag-on one adds the overlay
  // row and the brush), so no placeholder can be the right height for both.
  // While the rollout answer is unknown the page stays in its loading state;
  // the card then mounts ONCE, in its final form — whichever the answer.
  it.each([true, false])('holds the page until the rollout answers after the data (flag %s): the pass-rate card mounts once, in its final form', async (on) => {
    rollout.on = on
    rollout.pending = true
    const { useTrendData, useDashboardSummary, useCoverage, useFlakyTests } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({ data: { data: await activeDays(10) }, isLoading: false })
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({ data: {} })
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({ data: { summary: {}, suites: [] } })
    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    // A fresh element each time, so a rerender reaches the page (as an SWR answer would).
    const ui = () => (
      <MemoryRouter initialEntries={['/trends']}>
        <Routes><Route path="/trends" element={<TrendsPage />} /></Routes>
      </MemoryRouter>
    )
    const { rerender } = render(ui())
    // The data is in; the flag is not: no card yet, in either form.
    expect(screen.queryByRole('heading', { name: 'Pass rate trend' })).toBeNull()
    expect(frames.timeSeries).toHaveLength(0)
    expect(frames.timeSeriesMounts).toBe(0)

    rollout.pending = false
    rerender(ui())
    await screen.findByRole('heading', { name: 'Pass rate trend' })
    // Mounted once, and every render of it is the final form, at the same height.
    expect(frames.timeSeriesMounts).toBe(1)
    const renders = frames.timeSeries.map((p) => p as TimeSeriesProps)
    for (const p of renders) {
      expect(p.trendAnalysis).toBe(on ? true : undefined)
      expect(p.height).toBe(240)
    }
    expect(document.querySelector('[data-catalogue-section="trends-pass-rate"]') !== null).toBe(on)
  })
})

/**
 * The flag-off page's DOM is the Wave 2.5 page's DOM (plan 6.4, B3).
 *
 * The snapshot below was RECORDED FROM THE UNTOUCHED PAGE (`TrendsPage.tsx` at
 * 0267bac3 = Wave 2.5 main, put back in place for one run) and is compared
 * against this branch's page with the flag off. The normalisation removes
 * exactly the three intended flag-independent changes (plan 6.6 item 2), so
 * anything else — an extra wrapper, a new attribute, a section rendered with
 * the flag off — fails here:
 *   - the narrow-screen banner (deleted);
 *   - `grid-cols-1` / `lg:grid-cols-[...]` (the responsive grid classes);
 *   - `grid-template-columns` in a style attribute (the old fixed grids),
 *     which at >= 1024 px compute to the same columns as the new classes.
 * Ids are renumbered in document order: module-level counters (Recharts
 * clip paths, `useId`) depend on how many tests ran first.
 */
describe('TrendsPage flag-off DOM (Wave 2.6)', () => {
  const NOW = Date.parse('2026-09-15T12:00:00Z')

  beforeEach(async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(NOW)
    analyticsControls.widgetIds = ['trends_kpis', 'daily_breakdown', 'pass_rate_trend']
    rollout.on = false
    const { useTimeWindowStore } = await import('@/store/timeWindowStore')
    useTimeWindowStore.setState({ days: 30 })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  // The intended VIZ-106 change to the body grid's columns. The other one,
  // R2-4 (the workflow ribbon's three columns and their dividers moved from
  // inline styles to lg-prefixed classes so the steps stack below lg), is IN
  // the snapshot: its three buttons and their grid are the only lines that
  // fix changed.
  const RESPONSIVE_CLASS = /^(?:grid-cols-1|lg:grid-cols-\[.*\])$/

  function normalisedDom(root: HTMLElement): string {
    const clone = root.cloneNode(true) as HTMLElement
    for (const el of [...clone.querySelectorAll('div')]) {
      if (el.children.length === 0 && /^Wider screen needed/.test(el.textContent ?? '')) el.remove()
    }
    // P1 (2026-10-04): the header's own Views button, the one intended addition
    // to the Wave 2.5 header; asserted on its own below.
    for (const el of [...clone.querySelectorAll('[data-saved-views-trigger]')]) el.remove()
    const ids = new Map<string, string>()
    for (const el of [clone, ...clone.querySelectorAll('*')]) {
      if (el.id && !ids.has(el.id)) ids.set(el.id, `id-${ids.size + 1}`)
    }
    const byLength = [...ids.keys()].sort((a, b) => b.length - a.length)
    for (const el of [clone, ...clone.querySelectorAll('*')]) {
      const cls = el.getAttribute('class')
      if (cls !== null) {
        el.setAttribute('class', cls.split(/\s+/).filter((c) => c && !RESPONSIVE_CLASS.test(c)).join(' '))
      }
      const style = el.getAttribute('style')
      if (style !== null) {
        const kept = style.split(';').map((d) => d.trim()).filter((d) => d && !d.startsWith('grid-template-columns'))
        if (kept.length === 0) el.removeAttribute('style')
        else el.setAttribute('style', `${kept.join('; ')};`)
      }
      for (const attr of [...el.attributes]) {
        let value = attr.value
        for (const id of byLength) if (value.includes(id)) value = value.split(id).join(ids.get(id) as string)
        if (value !== attr.value) el.setAttribute(attr.name, value)
      }
    }
    return clone.innerHTML
  }

  it('renders the Wave 2.5 page, apart from the intended responsive classes and the banner', async () => {
    const { useTrendData, useDashboardSummary, useCoverage, useFlakyTests } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        data: [
          { date: '2026-09-14', passed: 31, failed: 17, skipped: 6, broken: 6, total: 60, pass_rate: 57.4 },
          { date: '2026-09-12', passed: 10, failed: 0, skipped: 0, broken: 2, total: 12, pass_rate: 83.3 },
          { date: '2026-09-10', passed: 0, failed: 0, skipped: 5, broken: 0, total: 5, pass_rate: 0 },
          { date: '2026-09-08', passed: 20, failed: 0, skipped: 0, broken: 0, total: 20, pass_rate: 100 },
        ],
      },
      isLoading: false,
    })
    ;(useDashboardSummary as ReturnType<typeof vi.fn>).mockReturnValue({ data: { avg_pass_rate_7d: { value: 71, trend: -2 } } })
    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        summary: {},
        suites: [
          { suite_name: 'Checkout', unique_tests: 6, passed: 40, failed: 10, skipped: 5, pass_rate: 80 },
          { suite_name: 'Payments', unique_tests: 4, passed: 21, failed: 21, skipped: 0, pass_rate: 50 },
        ],
      },
    })
    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [{ test_fingerprint: 'f1' }] } })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    const { container } = render(
      <MemoryRouter initialEntries={['/trends']}>
        <Routes><Route path="/trends" element={<TrendsPage />} /></Routes>
      </MemoryRouter>,
    )
    await screen.findByRole('heading', { name: 'Pass rate trend' })
    expect(normalisedDom(container)).toMatchSnapshot()
    // The Views button opens the header's actions, before Customize.
    const trigger = container.querySelector('[data-saved-views-trigger]')
    expect(trigger?.textContent).toBe('Views')
    expect(trigger?.nextElementSibling?.textContent).toContain('Customize')
  })
})
