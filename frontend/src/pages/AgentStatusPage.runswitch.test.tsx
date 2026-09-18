/**
 * Choosing a different run must not leave the previous run's pipeline selected.
 *
 * Regression for TL-2026-09-18-01-009 (user-reported 2026-09-18: "the dropdown
 * 'Pipeline Runs' — when a run is selected the data in the page is not
 * refreshed").
 *
 * The dropdown navigates to `/agents/run/:runId`, so `runId` changes and
 * `usePipelines(runId)` refetches correctly. But `selectedPipeline` is
 * component state, and it was only ever cleared on a project change
 * (`useProjectChangeReset`) or by clicking a pipeline card. Every detail panel
 * is keyed on it — `usePipelineStages`, `usePipelineTimeline`, the compute
 * graph and the AI report — so the page header moved to the new run while the
 * stages, timeline and report kept rendering the old one.
 *
 * The assertion is on the id the detail hooks are CALLED with, not on rendered
 * text: the panels are what went stale, and they are driven entirely by that
 * argument. Asserting on text would pass as soon as any panel rendered
 * anything, including the stale panel.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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
  useRuns: vi.fn(() => ({
    data: {
      items: [
        { id: 'run-1', build_number: '41', primary_suite_name: 'SuiteA' },
        { id: 'run-2', build_number: '42', primary_suite_name: 'SuiteB' },
      ],
    },
    isLoading: false,
  })),
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: vi.fn(() => ({ isQaEngineer: false })),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) =>
    selector(mockProjectState)),
}))

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

const pipelineForRun1 = {
  id: 'pipe-1',
  test_run_id: 'run-1',
  workflow_type: 'offline',
  status: 'completed',
  started_at: '2026-03-31T10:00:00Z',
  completed_at: '2026-03-31T10:01:00Z',
  error: null,
  created_at: '2026-03-31T10:00:00Z',
}

const lastArgOf = (fn: ReturnType<typeof vi.fn>): unknown => {
  // `.at(-1)` is not in this tsconfig's lib target, and `npm run type-check`
  // is a CI gate.
  const { calls } = fn.mock
  return calls.length ? calls[calls.length - 1][0] : undefined
}

describe('AgentStatusPage — switching the selected run', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockProjectState.activeProjectId = 'proj-1'
    mockProjectState.activeProject = { id: 'proj-1', name: 'Project One' }
  })

  it('clears the previous run pipeline so the detail panels cannot go stale', async () => {
    const { useActiveLiveRuns, usePipelineStages, usePipelineTimeline, usePipelines, useRunSummary } =
      await import('@/hooks/useAgentRuns')

    ;(useActiveLiveRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: [] })
    ;(usePipelines as ReturnType<typeof vi.fn>).mockReturnValue({
      data: [pipelineForRun1],
      isLoading: false,
    })
    ;(usePipelineStages as ReturnType<typeof vi.fn>).mockReturnValue({ data: [], isLoading: false })
    ;(usePipelineTimeline as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined })
    ;(useRunSummary as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      isLoading: false,
      error: undefined,
    })

    render(
      <MemoryRouter initialEntries={['/agents/run/run-1']}>
        <Routes>
          <Route path="/agents" element={<AgentStatusPage />} />
          <Route path="/agents/run/:runId" element={<AgentStatusPage />} />
        </Routes>
      </MemoryRouter>,
    )

    // Pick run-1's pipeline: the detail panels are now keyed on it.
    fireEvent.click(screen.getByRole('button', { name: /offline pipeline/i }))
    await waitFor(() => {
      expect(usePipelineStages).toHaveBeenCalledWith('pipe-1')
    })

    // Now switch runs through the dropdown, exactly as the user did.
    // Labelled 'Test Suite & Build:' in the markup; the user calls it the
    // Pipeline Runs dropdown after the section heading above it.
    const dropdown = screen.getByLabelText(/test suite/i)
    fireEvent.change(dropdown, { target: { value: 'run-2' } })

    // The detail hooks must stop being asked for run-1's pipeline. Before the
    // fix they kept receiving 'pipe-1' forever, so the stages, timeline and AI
    // report below the header still described the run the user had navigated
    // away from.
    await waitFor(() => {
      const lastStagesArg = lastArgOf(usePipelineStages as ReturnType<typeof vi.fn>)
      expect(
        lastStagesArg,
        'the previous run pipeline is still driving the stage panel',
      ).toBeNull()
    })

    const lastTimelineArg = lastArgOf(usePipelineTimeline as ReturnType<typeof vi.fn>)
    expect(
      lastTimelineArg,
      'the previous run pipeline is still driving the timeline',
    ).toBeNull()
  })
})
