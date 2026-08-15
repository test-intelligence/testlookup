import { fireEvent, render, screen } from '@testing-library/react'
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
  // Phase 4 verdicts. Returns empty so these tests assert the page renders
  // identically WITHOUT attribution — the annotation must never be load-bearing.
  useRunAttribution: vi.fn(() => ({ data: { items: [], total: 0 } })),
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

  // The gap that let the verdict ship with unreachable evidence: the component
  // tests exercised the expanded panel directly, and this file only checked
  // that a badge appeared. Nobody asserted the evidence was reachable FROM THE
  // PAGE, which is the compact path and the only path that ships.
  it('reaches the verdict evidence from the run detail table', async () => {
    const { useRun, useRunAttribution, useTestCases } = await import('@/hooks/useRuns')
    const useSWR = (await import('swr')).default as ReturnType<typeof vi.fn>

    const attribution = {
      test_case_id: 'tc-1',
      test_name: 'test_checkout',
      suite_name: 'checkout',
      verdict: 'UNCERTAIN' as const,
      verdict_label: 'Uncertain',
      verdict_description: 'signals disagree',
      confidence: 0,
      rationale: 'No signal was strong enough to attribute this failure.',
      inputs: {
        is_new_failure: true,
        last_green_run_id: 'run-0',
        flaky_score: null,
        flaky_confidence: 'none',
        cluster_key: null,
        cluster_cause_family: null,
        cluster_size: 0,
        change_overlap: null,
        changed_files: [],
        calibration_mode: 'hint',
        calibration_specificity: null,
      },
      votes: {},
      policy: 'Advisory only. No verdict suppresses, hides or auto-closes a failure.',
    }
    ;(useRunAttribution as ReturnType<typeof vi.fn>).mockReturnValue({
      data: { items: [attribution], total: 1 },
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
      data: {
        items: [
          {
            id: 'tc-1',
            test_name: 'test_checkout',
            suite_name: 'checkout',
            status: 'FAILED',
            duration_ms: 100,
            failure_category: null,
            failure_kind: null,
          },
        ],
        pages: 1,
        total: 1,
      },
      isLoading: false,
      error: undefined,
    })
    useSWR.mockReturnValue({ data: undefined, isLoading: false })

    render(
      <MemoryRouter initialEntries={['/runs/run-1']}>
        <Routes>
          <Route path="/runs/:runId" element={<RunDetailPage />} />
        </Routes>
      </MemoryRouter>,
    )

    const trigger = await screen.findByRole('button', { name: /why this verdict/i })
    fireEvent.click(trigger)

    // All five composed signals, reachable from the page as shipped.
    expect(screen.getByText('New failure')).toBeInTheDocument()
    expect(screen.getByText('Flakiness')).toBeInTheDocument()
    expect(screen.getByText('Co-failure cluster')).toBeInTheDocument()
    expect(screen.getByText('Change overlap')).toBeInTheDocument()
    expect(screen.getByText('Classifier calibration')).toBeInTheDocument()
    // And the advisory policy, which must never be buried.
    expect(screen.getByText(/no verdict suppresses/i)).toBeInTheDocument()
  })
})
