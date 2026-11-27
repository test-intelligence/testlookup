/**
 * D-153: a pipeline row shows the retry it hides, and DEGRADED says why.
 *
 * A same-id resume restarts the row's clock, so "Duration: 212s" counted only the last attempt and nothing showed
 * that there had been one. The DEGRADED tag had no title at all. The row now adds "attempt 2 of 2 (8 min 5 s in
 * total)" from `attempt` and `created_at`, and the tag's title lists the stages that failed or used their fallback
 * (`stage_quality_detail`, attached by the list route).
 */
import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AgentStatusPage from './AgentStatusPage'

const { mockProjectState } = vi.hoisted(() => ({
  mockProjectState: {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
  },
}))

vi.mock('@/hooks/useAgentRuns', () => ({
  usePipelines: vi.fn(),
  usePipelineStages: vi.fn(),
  usePipelineTimeline: vi.fn(),
  useRunSummary: vi.fn(),
  useActiveLiveRuns: vi.fn(),
}))
vi.mock('@/hooks/useAIConfig', () => ({
  useAIConfig: vi.fn(() => ({ data: { analysis_mode: 'auto', deep_investigation_enabled: true } })),
}))
vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(() => ({ data: { items: [] }, isLoading: false })),
}))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: vi.fn(() => ({ isQaEngineer: false })),
}))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) =>
    selector(mockProjectState)),
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

function pipeline(i: number, fields: Record<string, unknown>) {
  return {
    id: `pipe-${i}`,
    test_run_id: `run-${i}`,
    workflow_type: 'deep',
    status: 'completed',
    public_status: 'completed',
    // Queued at 10:00:00; the second attempt started 10:04:33 and finished 212 s later, 10:08:05.
    created_at: '2026-11-21T10:00:00Z',
    started_at: '2026-11-21T10:04:33Z',
    completed_at: '2026-11-21T10:08:05Z',
    error: null,
    attempt: 1,
    max_attempts: 5,
    execution_metadata: {},
    provenance_metadata: null,
    build_number: `b-${i}`,
    run_seq: i + 1,
    suite_name: null,
    ...fields,
  }
}

async function renderWith(pipelines: unknown[]) {
  const { useActiveLiveRuns, usePipelineStages, usePipelineTimeline, usePipelines, useRunSummary } =
    await import('@/hooks/useAgentRuns')
  ;(useActiveLiveRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: [] })
  ;(usePipelines as ReturnType<typeof vi.fn>).mockReturnValue({ data: pipelines, isLoading: false })
  ;(usePipelineStages as ReturnType<typeof vi.fn>).mockReturnValue({ data: [], isLoading: false })
  ;(usePipelineTimeline as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined, isLoading: false })
  ;(useRunSummary as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined, isLoading: false, error: undefined })
  render(
    <MemoryRouter initialEntries={['/agents']}>
      <Routes>
        <Route path="/agents" element={<AgentStatusPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('AgentStatusPage — attempts and the DEGRADED reason (D-153)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('a resumed pipeline shows its attempt and the total time since it was queued', async () => {
    await renderWith([pipeline(0, { attempt: 2 })])
    const row = (await screen.findAllByTestId('pipeline-row'))[0]
    expect(row).toHaveTextContent('Duration: 212s')
    expect(screen.getByTestId('pipeline-attempt')).toHaveTextContent('· attempt 2 of 2 (8 min 5 s in total)')
  })

  it('a first attempt, and a row still running, show no attempt line', async () => {
    await renderWith([
      pipeline(0, { attempt: 1 }),
      pipeline(1, { attempt: 2, status: 'running', public_status: 'in_progress', completed_at: null }),
    ])
    await screen.findAllByTestId('pipeline-row')
    expect(screen.queryByTestId('pipeline-attempt')).toBeNull()
  })

  it('the DEGRADED tag explains which stages failed or used their fallback', async () => {
    await renderWith([
      pipeline(0, {
        execution_metadata: { stage_quality: 'degraded' },
        stage_quality_detail: {
          stages_run: 4,
          issues: [
            { stage_name: 'root_cause_analysis', status: 'failed', fallback_reason: null, error_category: 'timeout' },
            { stage_name: 'analysis', status: 'completed', fallback_reason: '2/4 tests fell back from requested engine', error_category: null },
          ],
        },
      }),
    ])
    expect(await screen.findByTestId('pipeline-quality-tag')).toHaveAttribute(
      'title',
      '2 of 4 steps did not finish cleanly: root cause analysis failed (timed out); analysis used its fallback (2/4 tests fell back from requested engine)',
    )
  })

  it('a degraded row without the detail still has a title', async () => {
    await renderWith([pipeline(0, { execution_metadata: { stage_quality: 'degraded' } })])
    expect(await screen.findByTestId('pipeline-quality-tag')).toHaveAttribute(
      'title',
      'Some steps did not finish cleanly; the run finished without them.',
    )
  })
})
