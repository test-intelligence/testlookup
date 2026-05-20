import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

import RunDetailPage from './RunDetailPage'

const { mockProjectState } = vi.hoisted(() => ({
  mockProjectState: {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
  },
}))

vi.mock('@/hooks/useRuns', () => ({
  useRun: vi.fn(),
  // The page added a sibling-runs fetch (``useRuns``) to power the
  // suite-aware run comparison strip. Mock must export it.
  useRuns: vi.fn(),
  useTestCases: vi.fn(),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) =>
    selector(mockProjectState)),
}))

vi.mock('@/services/runsService', () => ({
  runsService: {
    setRelease: vi.fn(),
    get: vi.fn(),
    list: vi.fn(),
    listTests: vi.fn(),
    getTest: vi.fn(),
  },
}))

vi.mock('swr', () => ({
  default: vi.fn(),
  mutate: vi.fn(),
}))

describe('RunDetailPage', () => {
  it('returns to the runs list when the project changes', async () => {
    const { useRun, useRuns, useTestCases } = await import('@/hooks/useRuns')
    const useSWR = (await import('swr')).default as ReturnType<typeof vi.fn>

    ;(useRuns as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [] },
      isLoading: false,
    })

    ;(useRun as ReturnType<typeof vi.fn>).mockReturnValue({
      data: {
        id: 'run-1',
        build_number: '42',
        jenkins_job: 'job-1',
        created_at: '2026-03-31T10:00:00Z',
        release_name: null,
        passed_tests: 10,
        failed_tests: 2,
        skipped_tests: 0,
        total_tests: 12,
        status: 'failed',
      },
    })
    ;(useTestCases as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [], pages: 1, total: 0 },
      isLoading: false,
      error: undefined,
    })
    useSWR.mockReturnValue({ data: undefined, isLoading: false })

    const { rerender } = render(
      <MemoryRouter initialEntries={['/runs/run-1']}>
        <Routes>
          <Route path="/runs" element={<div>Runs List</div>} />
          <Route path="/runs/:runId" element={<RunDetailPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Run #42/i)).toBeInTheDocument()

    mockProjectState.activeProjectId = 'proj-2'
    mockProjectState.activeProject = { id: 'proj-2', name: 'Project Two' }

    rerender(
      <MemoryRouter initialEntries={['/runs/run-1']}>
        <Routes>
          <Route path="/runs" element={<div>Runs List</div>} />
          <Route path="/runs/:runId" element={<RunDetailPage />} />
        </Routes>
      </MemoryRouter>,
    )

    expect(await screen.findByText(/Runs List/i)).toBeInTheDocument()
  })
})
