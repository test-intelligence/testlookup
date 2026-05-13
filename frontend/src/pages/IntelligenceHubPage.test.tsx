import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import IntelligenceHubPage from './IntelligenceHubPage'

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
}))

vi.mock('@/store/projectStore', () => {
  const ALL_PROJECTS_ID = 'all'
  return {
    ALL_PROJECTS_ID,
    useProjectStore: (selector: (s: { activeProjectId: string }) => unknown) =>
      selector({ activeProjectId: ALL_PROJECTS_ID }),
  }
})

describe('IntelligenceHubPage', () => {
  it('renders the verdict cockpit and runs table with the new design', async () => {
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        items: [
          { id: 'run-1', build_number: '42', status: 'PASSED', failed_tests: 0,  passed_tests: 120, broken_tests: 0, skipped_tests: 0, total_tests: 120, pass_rate: 100, branch: 'main', project_name: 'Project One', created_at: '2026-04-03T15:00:00Z', duration_ms: 492000 },
          { id: 'run-2', build_number: '41', status: 'PASSED', failed_tests: 0,  passed_tests: 119, broken_tests: 1, skipped_tests: 0, total_tests: 120, pass_rate: 99.2,  branch: 'main', project_name: 'Project One', created_at: '2026-04-02T15:00:00Z', duration_ms: 664000 },
        ],
      },
      isLoading: false,
    })

    render(
      <MemoryRouter initialEntries={['/intelligence']}>
        <Routes>
          <Route path="/intelligence" element={<IntelligenceHubPage />} />
        </Routes>
      </MemoryRouter>,
    )

    // Header
    expect(await screen.findByText(/Run Intelligence/i)).toBeInTheDocument()
    // Verdict eyebrow
    expect(screen.getByText(/Pipeline health/i)).toBeInTheDocument()
    // Composite health number is rendered
    expect(screen.getByText('/100')).toBeInTheDocument()
    // Runs table is present with the redesigned title
    expect(screen.getByText(/Recent runs analyzed/i)).toBeInTheDocument()
    // Specific run rows show up — they appear both in the table cell and in
    // the activity feed, so getAllByText is the right query.
    expect(screen.getAllByText('#42').length).toBeGreaterThan(0)
    expect(screen.getAllByText('#41').length).toBeGreaterThan(0)
  })
})
