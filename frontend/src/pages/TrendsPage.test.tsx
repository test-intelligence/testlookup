import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import TrendsPage from './TrendsPage'

vi.mock('@/hooks/useMetrics', () => ({
  useTrendData: vi.fn(),
  useDashboardSummary: vi.fn(),
  useCoverage: vi.fn(),
  useFlakyTests: vi.fn(),
}))

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
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

describe('TrendsPage', () => {
  beforeEach(() => {
    analyticsControls.widgetIds = ['trends_kpis', 'daily_breakdown', 'pass_rate_trend']
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
    // The tick that claims to be today must name today, not the day before.
    expect(screen.getByText(`${formatDayIso(todayIso)} (today)`)).toBeInTheDocument()
    expect(screen.queryByText(`${formatDayIso(shiftDayIso(todayIso, -1))} (today)`)).not.toBeInTheDocument()
    // The 14-day window opens 13 days back, and that label must be right too.
    expect(screen.getAllByText(formatDayIso(shiftDayIso(todayIso, -13))).length).toBeGreaterThan(0)
  })
})
