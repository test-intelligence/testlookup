import { render, screen, fireEvent, within } from '@testing-library/react'
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

  it('renders an inline current-vs-prior comparison when "Compare to previous window" is clicked', async () => {
    // Pin the user-visible bug fix: the CTA used to navigate to /trends,
    // which didn't show any comparison. Now the CTA toggles an inline
    // strip computed from a double-window trend fetch.

    // The window selector persists via localStorage (key
    // ``tl.failures.window.v2``) and defaults to 1 (last 24h) if absent.
    // Force 30 so the strip's right-slot label is deterministic.
    localStorage.setItem('tl.failures.window.v2', '30')

    const { useFlakyTests, useFailureCategories, useTopFailing, useTrendData } = await import('@/hooks/useMetrics')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useFlakyTests as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useFailureCategories as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useTopFailing as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] }, isLoading: false })

    // 4 trend points — 2 prior (low failures) + 2 current (high failures).
    // The strip should compute a +3 failure delta and render it.
    ;(useTrendData as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        data: [
          { date: '2026-04-01', passed: 10, failed: 1, skipped: 0, broken: 0, total: 11, pass_rate: 90 },
          { date: '2026-04-02', passed: 10, failed: 1, skipped: 0, broken: 0, total: 11, pass_rate: 90 },
          { date: '2026-04-03', passed: 8,  failed: 3, skipped: 0, broken: 0, total: 11, pass_rate: 72 },
          { date: '2026-04-04', passed: 7,  failed: 2, skipped: 0, broken: 0, total: 9,  pass_rate: 78 },
        ],
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/failure-analysis?days=30']}>
        <Routes>
          <Route path="/failure-analysis" element={<FailureAnalysisPage />} />
        </Routes>
      </MemoryRouter>,
    )

    // Before the click, no comparison strip should be rendered.
    expect(screen.queryByText(/last \d+d vs prior \d+d/i)).toBeNull()

    // The "Compare to previous window" action lives in the verdict CTAs.
    // Click it — page state flips and the inline panel appears.
    fireEvent.click(await screen.findByText(/Compare to previous window/i))

    // The strip's right-slot label is unique on the page — use it as
    // the "strip is rendered" signal. The toggle CTA also flips its
    // text to "Hide comparison" when on, which is the other unique
    // signal we can rely on without coupling to the strip's internals.
    expect(await screen.findByText(/last 30d vs prior 30d/i)).toBeInTheDocument()
    // Prior failures = 1+1 = 2; current failures = 3+2 = 5; delta = +3.
    // The failures cell renders the up-arrow with "3" — pin that one
    // delta value as the proof the math ran. Use a word-boundary so it
    // doesn't also match "↑ 33".
    expect(screen.getByText(/↑\s*3\b/)).toBeInTheDocument()

    // Re-clicking hides the panel.
    fireEvent.click(screen.getByText(/Hide comparison/i))
    expect(screen.queryByText(/last \d+d vs prior \d+d/i)).toBeNull()
  })
})
