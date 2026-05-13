import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import FailureAnalysisPage from './FailureAnalysisPage'

vi.mock('@/hooks/useMetrics', () => ({
  useFlakyTests: vi.fn(),
  useFailureCategories: vi.fn(),
  useTopFailing: vi.fn(),
  useTrendData: vi.fn(),
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

describe('FailureAnalysisPage', () => {
  it('renders the failure analysis workflow strip above the charts', async () => {
    const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ test_name: 'test A', failure_rate_pct: 42 }] },
      isLoading: false,
    })
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ category: 'INFRA_FAILURE', count: 4 }] },
      isLoading: false,
    })
    ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [{ test_name: 'test A', fail_count: 4 }] },
      isLoading: false,
    })
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { data: [{ date: '2026-04-01', passed: 10, failed: 2, skipped: 0, broken: 0, pass_rate: 83 }] },
      isLoading: false,
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })

    render(
      <MemoryRouter initialEntries={['/failure-analysis']}>
        <Routes>
          <Route path="/failure-analysis" element={<FailureAnalysisPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Failure analysis workflow/i)).toBeInTheDocument()
    expect(screen.getAllByText(/Flaky Detection/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/Failure Analysis/i).length).toBeGreaterThan(0)
  })
})
