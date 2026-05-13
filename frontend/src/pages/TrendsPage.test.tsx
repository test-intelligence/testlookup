import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

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

vi.mock('@/hooks/useAnalyticsView', () => ({
  useAnalyticsView: () => ({
    widgetIds: [],
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
})
