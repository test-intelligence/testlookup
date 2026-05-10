import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import OverviewPage from './OverviewPage'

vi.mock('@/hooks/useMetrics', () => ({
  useDashboardSummary: vi.fn(),
  useTrendData: vi.fn(),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string; activeProject: { name: string } | null }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { name: 'Project One' } })),
}))

describe('OverviewPage', () => {
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

    expect(await screen.findByText(/Quality workflow/i)).toBeInTheDocument()
    expect(screen.getByText(/^Dashboard$/i)).toBeInTheDocument()
    expect(screen.getByText(/Release Readiness: GREEN/i)).toBeInTheDocument()
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

    expect(await screen.findByText(/Release Readiness: Pending/i)).toBeInTheDocument()
    // The misleading "Critical issues" copy must NOT be on the page.
    expect(screen.queryByText(/Critical issues must be resolved/i)).toBeNull()
  })
})
