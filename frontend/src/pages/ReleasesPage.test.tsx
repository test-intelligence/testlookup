import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import ReleasesPage from './ReleasesPage'

vi.mock('@/hooks/useReleases', () => ({
  useReleases: vi.fn(),
  useRelease: vi.fn(),
}))

vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: { activeProjectId: string; activeProject: { id: string; name: string } | null }) => unknown) =>
    selector({ activeProjectId: 'proj-1', activeProject: { id: 'proj-1', name: 'Project One' } })),
}))

describe('ReleasesPage', () => {
  it('renders the release summary row with the current hook contracts', async () => {
    const { useRelease, useReleases } = await import('@/hooks/useReleases')
    const { useRuns } = await import('@/hooks/useRuns')

    ;(useRelease as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        id: 'release-1',
        project_id: 'proj-1',
        project_name: 'Project One',
        name: 'v2.4.0',
        version: '2.4.0',
        description: 'Login revamp',
        status: 'in_progress',
        planned_date: '2026-04-03T00:00:00Z',
        released_at: null,
        created_at: '2026-04-03T00:00:00Z',
        updated_at: '2026-04-03T00:00:00Z',
        phases: [],
        test_run_count: 0,
        linked_runs: [],
        metrics: {
          total_runs: 0,
          total_tests: 0,
          total_passed: 0,
          total_failed: 0,
          avg_pass_rate: null,
        },
      },
      isLoading: false,
      mutate: vi.fn(),
    })
    ;(useReleases as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        items: [
          {
            id: 'release-1',
            project_id: 'proj-1',
            project_name: 'Project One',
            name: 'v2.4.0',
            version: '2.4.0',
            description: 'Login revamp',
            status: 'in_progress',
            planned_date: '2026-04-03T00:00:00Z',
            released_at: null,
            created_at: '2026-04-03T00:00:00Z',
            updated_at: '2026-04-03T00:00:00Z',
            phases: [],
            test_run_count: 0,
            linked_runs: [],
            metrics: {
              total_runs: 0,
              total_tests: 0,
              total_passed: 0,
              total_failed: 0,
              avg_pass_rate: null,
            },
          },
        ],
      },
      isLoading: false,
      mutate: vi.fn(),
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })

    render(
      <MemoryRouter initialEntries={['/releases/release-1']}>
        <Routes>
          <Route path="/releases/:releaseId" element={<ReleasesPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByRole('heading', { name: /Releases/i })).toBeInTheDocument()
    expect(screen.getByText(/v2\.4\.0/i)).toBeInTheDocument()
    expect(screen.getAllByText(/In Progress/i).length).toBeGreaterThan(0)
  })
})
