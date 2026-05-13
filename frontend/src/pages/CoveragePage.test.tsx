import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import CoveragePage from './CoveragePage'

// Mock every export from useMetrics — the page (and any child it renders)
// may pull in more hooks than the test exercises, and vitest errors out if
// an imported export isn't defined on the mock module. Defaults to an
// empty SWR shape; individual tests can ``mockReturnValue`` to override.
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
// Other hooks the page transitively imports — stubbed so SWR doesn't fire.
vi.mock('@/hooks/useSuiteOptions', () => ({
  useSuiteOptions: () => ({ options: [], isLoading: false }),
}))
vi.mock('@/hooks/useRuns', () => ({
  useRuns: () => ({ data: { items: [] }, isLoading: false }),
}))
vi.mock('@/hooks/useAnalyticsView', () => ({
  useAnalyticsView: () => ({
    instances: [], widgetIds: [], addInstance: vi.fn(), removeInstance: vi.fn(),
    save: vi.fn(), reset: vi.fn(), isDirty: false, savedViews: [],
    activeViewId: null, setActiveView: vi.fn(), deleteView: vi.fn(),
    updateInstance: vi.fn(), moveInstance: vi.fn(),
  }),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string; activeProject: { name: string } | null }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { name: 'Project One' } })),
}))

describe('CoveragePage', () => {
  it('renders the coverage workflow strip above the suite breakdown', async () => {
    const { useCoverage } = await import('@/hooks/useMetrics')

    ;(useCoverage as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        summary: {
          unique_tests: 18,
          suite_count: 4,
          total_executions: 120,
          avg_pass_rate: 92.5,
          days_with_runs: 7,
        },
        suites: [
          { suite_name: 'Payments', unique_tests: 6, passed: 48, failed: 2, skipped: 1, pass_rate: 96 },
          { suite_name: 'Auth', unique_tests: 4, passed: 32, failed: 3, skipped: 0, pass_rate: 91 },
        ],
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/coverage']}>
        <Routes>
          <Route path="/coverage" element={<CoveragePage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Coverage Workflow/i)).toBeInTheDocument()
    expect(screen.getByText(/Coverage Snapshot/i)).toBeInTheDocument()
    expect(screen.getByText(/Test Coverage/i)).toBeInTheDocument()
  })
})
