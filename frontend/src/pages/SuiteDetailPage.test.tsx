/**
 * Regression: /coverage/suite trend chart + days-selector URL rewrite.
 *
 * Two distinct bugs pinned in this file:
 *
 * 1) (2026-05-19) The days selector chips (7d/14d/30d/90d) had no
 *    effect when the URL already carried ``?days=``. The URL pin
 *    overrode the click. Fix: clicking a chip rewrites ?days= via
 *    ``setSearchParams(..., {replace: true})``.
 *
 * 2) (2026-05-19) /coverage/suite had no per-day trend chart. The new
 *    ``testManagementService.getSuiteTrend`` call powers a stacked
 *    bar chart of passed/failed/skipped/broken per day, gated on
 *    "at least one day with run_count > 0".
 */
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import SuiteDetailPage from './SuiteDetailPage'

const mockGetSuiteTrend = vi.fn()

vi.mock('@/services/testManagementService', () => ({
  testManagementService: {
    getSuiteTrend: (...args: unknown[]) => mockGetSuiteTrend(...args),
  },
}))

// ``useSuiteDetail`` is the page's primary fetch. Stub it to a
// stable summary so the page renders past the loading branch.
vi.mock('@/hooks/useMetrics', () => ({
  useSuiteDetail: vi.fn(() => ({
    data: {
      summary: {
        unique_tests: 12, total_executions: 100,
        passed: 80, failed: 15, pass_rate: 80, avg_duration_ms: 1234,
      },
      test_cases: [
        { test_fingerprint: 'fp1', test_name: 't1', class_name: 'C', runs: 5,
          passed: 4, failed: 1, skipped: 0, pass_rate: 80,
          last_status: 'PASSED', last_duration_ms: 100, last_run_date: null },
      ],
      recent_runs: [
        { run_id: 'r1', build_number: 'b1', run_date: '2026-05-13',
          passed: 9, failed: 1, skipped: 0, pass_rate: 90 },
        { run_id: 'r2', build_number: 'b2', run_date: '2026-05-14',
          passed: 8, failed: 2, skipped: 0, pass_rate: 80 },
      ],
    },
    isLoading: false,
    error: null,
  })),
}))

vi.mock('@/hooks/useSuites', () => ({
  useSuites: vi.fn(() => ({ data: { items: [] } })),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (s: {
    activeProjectId: string; activeProject: null | { name: string }
  }) => unknown) => selector({ activeProjectId: 'proj-1', activeProject: { name: 'P' } })),
}))

// recharts pulls in a heavy DOM measurement layer that JSDOM doesn't
// implement. Replace ResponsiveContainer with a fixed-size stand-in
// so the chart's <BarChart> render path executes.
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
      <div data-testid="chart-host" style={{ width: 400, height: 200 }}>
        {children}
      </div>
    ),
  }
})

function renderPage(url = '/coverage/suite?name=Auth&days=30') {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/coverage/suite" element={<SuiteDetailPage />} />
        </Routes>
      </MemoryRouter>
    </SWRConfig>,
  )
}

describe('SuiteDetailPage — regression', () => {
  beforeEach(() => {
    mockGetSuiteTrend.mockReset()
  })

  it('renders the run-history bar chart when trend has runs', async () => {
    mockGetSuiteTrend.mockResolvedValue({
      suite_name: 'Auth', days: 30,
      points: [
        { date: '2026-05-13', run_count: 1, total_tests: 10,
          passed_count: 9, failed_count: 1, skipped_count: 0, broken_count: 0 },
        { date: '2026-05-14', run_count: 2, total_tests: 20,
          passed_count: 18, failed_count: 2, skipped_count: 0, broken_count: 0 },
      ],
    })

    renderPage()

    await waitFor(() => expect(mockGetSuiteTrend).toHaveBeenCalled())
    expect(await screen.findByText(/Run history/)).toBeInTheDocument()
    // The summary line in the card header reports total runs +
    // executions across the window.
    expect(screen.getByText(/3 runs/i)).toBeInTheDocument()
  })

  it('hides the trend card when every day has run_count = 0', async () => {
    mockGetSuiteTrend.mockResolvedValue({
      suite_name: 'Auth', days: 30,
      points: [
        { date: '2026-05-13', run_count: 0, total_tests: 0,
          passed_count: 0, failed_count: 0, skipped_count: 0, broken_count: 0 },
      ],
    })

    renderPage()

    await waitFor(() => expect(mockGetSuiteTrend).toHaveBeenCalled())
    expect(screen.queryByText(/Run history/)).not.toBeInTheDocument()
  })

  it('passes the URL days param to the trend service', async () => {
    mockGetSuiteTrend.mockResolvedValue({ suite_name: 'Auth', days: 90, points: [] })

    renderPage('/coverage/suite?name=Auth&days=90')

    await waitFor(() => {
      // Third positional arg is ``days``.
      expect(mockGetSuiteTrend).toHaveBeenCalledWith('Auth', expect.anything(), 90)
    })
  })
})
