import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import ReleasesPage from './ReleasesPage'
import { releasesService } from '@/services/releasesService'

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
    // v2.4.0 now appears in multiple places (release card + summary row) —
    // assert presence without binding to a specific surface.
    expect(screen.getAllByText(/v2\.4\.0/i).length).toBeGreaterThan(0)
    expect(screen.getAllByText(/In Progress/i).length).toBeGreaterThan(0)
    expect(screen.getByRole('heading', { name: 'Linked Test Runs (0)' })).toBeVisible()
  })

  it('records a reasoned incident from the expanded release detail', async () => {
    const { useRelease, useReleases } = await import('@/hooks/useReleases')
    const { useRuns } = await import('@/hooks/useRuns')
    const refetch = vi.fn()
    const release = {
      id: 'release-1',
      project_id: 'proj-1',
      project_name: 'Project One',
      name: 'v2.4.0',
      version: '2.4.0',
      description: 'Login revamp',
      status: 'released',
      planned_date: '2026-04-03T00:00:00Z',
      released_at: '2026-04-03T00:00:00Z',
      created_at: '2026-04-03T00:00:00Z',
      updated_at: '2026-04-03T00:00:00Z',
      phases: [],
      test_run_count: 0,
    }
    ;(useRelease as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        ...release,
        linked_runs: [],
        outcomes: [{
          id: 'outcome-1',
          release_id: 'release-1',
          project_id: 'proj-1',
          outcome_kind: 'rollback',
          reason: 'Database saturation after rollout',
          marked_by_user_id: 'user-1',
          marked_at: '2026-04-04T00:00:00Z',
        }],
        metrics: {
          total_runs: 0,
          total_tests: 0,
          total_passed: 0,
          total_failed: 0,
          avg_pass_rate: null,
        },
      },
      isLoading: false,
      mutate: refetch,
    })
    ;(useReleases as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [release] },
      isLoading: false,
      mutate: vi.fn(),
    })
    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: { items: [] } })
    const markOutcome = vi.spyOn(releasesService, 'markOutcome').mockResolvedValue({
      id: 'outcome-2',
      release_id: 'release-1',
      project_id: 'proj-1',
      outcome_kind: 'incident',
      reason: 'Checkout failures increased after deploy',
      marked_by_user_id: 'user-1',
      marked_at: '2026-04-05T00:00:00Z',
    })

    render(
      <MemoryRouter initialEntries={['/releases']}>
        <Routes>
          <Route path="/releases" element={<ReleasesPage />} />
        </Routes>
      </MemoryRouter>,
    )

    fireEvent.click(screen.getByLabelText(/v2\.4\.0 2\.4\.0/))
    expect(await screen.findByRole('heading', { name: 'Production outcome' })).toBeInTheDocument()
    expect(screen.getByText('Database saturation after rollout')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Reason'), {
      target: { value: 'Checkout failures increased after deploy' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Mark incident' }))

    await waitFor(() => {
      expect(markOutcome).toHaveBeenCalledWith(
        'release-1',
        'incident',
        'Checkout failures increased after deploy',
      )
    })
    expect(refetch).toHaveBeenCalled()
  })
})
