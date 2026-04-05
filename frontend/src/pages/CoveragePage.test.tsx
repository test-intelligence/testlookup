import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import CoveragePage from './CoveragePage'

vi.mock('@/hooks/useMetrics', () => ({
  useCoverage: vi.fn(),
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
