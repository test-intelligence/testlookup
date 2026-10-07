/**
 * BUG-011 → UX redesign P5 (D3): ONE run picker, and the trigger acts on it.
 *
 * BUG-011 was reported twice: under the "Pipeline Runs" heading sat a second
 * select over the same recent runs as the header's picker, and "when any
 * value is selected, the displayed content of pipelines are not refreshed or
 * changed". That select was the **manual trigger** picker — its `onChange`
 * only armed the button beside it. The first fix labelled it ("Trigger a
 * pipeline manually — does not change the view below"); the redesign removes
 * the cause instead: the page has one run picker (it changes the view, see
 * `AgentStatusPage.runswitch.test.tsx`), and the trigger queues a pipeline for
 * the run picked there.
 *
 * Pinned here:
 *  - exactly one run picker, and no "Trigger a pipeline manually" select;
 *  - the trigger is disabled until a run is picked, then queues THAT run's
 *    pipeline (the id sent is asserted, not just "something was called");
 *  - the trigger is for QA engineers only, as before.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AgentStatusPage from './AgentStatusPage'

const { mockProjectState, permissions, triggerPipeline, toastApi } = vi.hoisted(() => ({
  mockProjectState: {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
  },
  permissions: { isQaEngineer: true },
  triggerPipeline: vi.fn(),
  toastApi: { success: vi.fn(), error: vi.fn() },
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
        { id: 'run-1', build_number: '41', run_seq: 41, primary_suite_name: 'AuthSuite', suite_names: ['AuthSuite'] },
        { id: 'run-2', build_number: '42', run_seq: 42, primary_suite_name: 'PaySuite', suite_names: ['PaySuite'] },
      ],
    },
    isLoading: false,
  })),
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: vi.fn(() => permissions),
}))

vi.mock('@/services/agentService', () => ({
  default: { triggerPipeline },
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) =>
    selector(mockProjectState)),
}))

vi.mock('react-hot-toast', () => ({ default: toastApi }))

async function renderPage(entry = '/agents') {
  const { useActiveLiveRuns, usePipelineStages, usePipelineTimeline, usePipelines, useRunSummary } =
    await import('@/hooks/useAgentRuns')

  ;(useActiveLiveRuns as ReturnType<typeof vi.fn>).mockReturnValue({ data: [] })
  ;(usePipelines as ReturnType<typeof vi.fn>).mockReturnValue({
    data: [
      {
        id: 'pipe-1', test_run_id: 'run-1', workflow_type: 'offline', status: 'completed',
        started_at: '2026-03-31T10:00:00Z', completed_at: '2026-03-31T10:01:00Z',
        error: null, created_at: '2026-03-31T10:00:00Z',
      },
    ],
    isLoading: false,
  })
  ;(usePipelineStages as ReturnType<typeof vi.fn>).mockReturnValue({ data: [], isLoading: false })
  ;(usePipelineTimeline as ReturnType<typeof vi.fn>).mockReturnValue({ data: undefined })
  ;(useRunSummary as ReturnType<typeof vi.fn>).mockReturnValue({
    data: undefined, isLoading: false, error: undefined,
  })

  render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/agents" element={<AgentStatusPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

const trigger = () => screen.getByRole('button', { name: /trigger pipeline/i })

describe('Pipeline runs — one run picker, and the trigger acts on it (BUG-011, P5)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    permissions.isQaEngineer = true
    triggerPipeline.mockResolvedValue({})
    mockProjectState.activeProjectId = 'proj-1'
    mockProjectState.activeProject = { id: 'proj-1', name: 'Project One' }
  })

  it('has exactly one run picker, and no separate trigger select', async () => {
    await renderPage()
    // The page has one <select>: the run picker. The trigger's own select (and
    // its "does not change the view" disclaimer) are gone with the cause.
    const selects = screen.getAllByRole('combobox')
    expect(selects).toHaveLength(1)
    expect(selects[0]).toBe(screen.getByLabelText(/test suite & build/i))
    expect(screen.queryByLabelText(/trigger a pipeline manually/i)).toBeNull()
    expect(screen.queryByText(/does not change the view/i)).toBeNull()
  })

  it('cannot trigger with no run picked: there is nothing to queue', async () => {
    await renderPage()
    expect(trigger()).toBeDisabled()
    expect(trigger()).toHaveAttribute('title', expect.stringMatching(/pick a test suite & build first/i))
    fireEvent.click(trigger())
    expect(triggerPipeline).not.toHaveBeenCalled()
  })

  it('queues the pipeline of the run picked in the one picker', async () => {
    await renderPage()
    fireEvent.change(screen.getByLabelText(/test suite & build/i), { target: { value: 'run-2' } })
    await waitFor(() => expect(trigger()).toBeEnabled())

    fireEvent.click(trigger())
    await waitFor(() => expect(triggerPipeline).toHaveBeenCalledTimes(1))
    // The run in the picker — the run on screen — not any other.
    expect(triggerPipeline).toHaveBeenCalledWith('run-2')
    await waitFor(() => expect(toastApi.success).toHaveBeenCalledWith('Pipeline queued — it will appear in the list shortly.'))
  })

  it('queues the run named by ?run= on arrival', async () => {
    await renderPage('/agents?run=run-1')
    expect((screen.getByLabelText(/test suite & build/i) as HTMLSelectElement).value).toBe('run-1')
    fireEvent.click(trigger())
    await waitFor(() => expect(triggerPipeline).toHaveBeenCalledWith('run-1'))
  })

  it('says why a trigger failed', async () => {
    triggerPipeline.mockRejectedValue({ response: { data: { detail: 'Run is still streaming' } } })
    await renderPage('/agents?run=run-1')
    fireEvent.click(trigger())
    await waitFor(() => expect(toastApi.error).toHaveBeenCalledWith('Run is still streaming'))
    expect(trigger()).toBeEnabled()
  })

  it('offers no trigger to a role below QA engineer', async () => {
    permissions.isQaEngineer = false
    await renderPage('/agents?run=run-1')
    expect(screen.queryByRole('button', { name: /trigger pipeline/i })).toBeNull()
    // The picker is everyone's.
    expect(screen.getByLabelText(/test suite & build/i)).toBeInTheDocument()
  })
})
