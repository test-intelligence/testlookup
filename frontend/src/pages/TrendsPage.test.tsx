import { fireEvent, render, screen, within } from '@testing-library/react'
import type { ComponentProps } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

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
  return {
    ...actual,
    default: (props: ComponentProps<typeof TimeSeriesChartFrame>) => {
      frames.timeSeries.push(props)
      return <Real {...props} />
    },
  }
})

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
  useSuiteOptions: () => ({ options: ['Checkout'], isLoading: false }),
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
