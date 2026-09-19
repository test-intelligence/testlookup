/**
 * BUG-011: the run dropdown under "Pipeline Runs" must not read as a view selector.
 *
 * User-reported twice. The second report — "when any value is selected, the
 * displayed content of pipelines are not refreshed or changed, it is static
 * values" — is an accurate observation of correct behaviour being *presented*
 * wrongly. That `<select>` is the **manual trigger** picker: its `onChange` only
 * calls `setTriggerInput`, arming the adjacent button. The pipeline shown below
 * is chosen by **clicking a card** (`setSelection`).
 *
 * The first report was filed as TL-2026-09-18-01-009 and fixed — but that was a
 * genuinely different defect (clicking a card left the previous run's data on
 * screen, fixed with derived selection). Fixing it could never have addressed
 * this, because this control was never meant to change the view.
 *
 * What made it misread: the control sits directly under a heading that says
 * "Pipeline Runs", lists runs by suite and build, and carried **no visible
 * label** — its purpose lived only in `aria-label` and a hover `title`, neither
 * of which a user sees before clicking.
 *
 * So the fix is presentational and deliberately changes no behaviour. These
 * tests pin the two halves of that: the control announces what it does, and
 * selecting in it still does not alter the displayed pipeline.
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
        { id: 'run-1', build_number: '41', run_seq: 41, primary_suite_name: 'AuthSuite', suite_names: ['AuthSuite'] },
        { id: 'run-2', build_number: '42', run_seq: 42, primary_suite_name: 'PaySuite', suite_names: ['PaySuite'] },
      ],
    },
    isLoading: false,
  })),
}))

// The control only renders for QA engineers.
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: vi.fn(() => ({ isQaEngineer: true })),
}))

vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) =>
    selector(mockProjectState)),
}))

vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

async function renderPage() {
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
    <MemoryRouter initialEntries={['/agents']}>
      <Routes>
        <Route path="/agents" element={<AgentStatusPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('BUG-011 — the trigger dropdown announces its purpose', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockProjectState.activeProjectId = 'proj-1'
    mockProjectState.activeProject = { id: 'proj-1', name: 'Project One' }
  })

  it('carries a visible label, not just an aria-label', async () => {
    await renderPage()
    // `getByText` reads rendered text. An aria-label would satisfy
    // `getByLabelText` while remaining invisible on screen, which is exactly the
    // state that produced two user reports.
    expect(screen.getByText(/trigger a pipeline manually/i)).toBeInTheDocument()
  })

  it('says it does not change the view', async () => {
    await renderPage()
    // The sentence a user needs in order not to file this bug a third time.
    expect(screen.getByText(/does not change the view below/i)).toBeInTheDocument()
  })

  it('the label is associated with the select', async () => {
    await renderPage()
    const select = screen.getByLabelText(/trigger a pipeline manually/i)
    expect(select.tagName).toBe('SELECT')
  })

  it('selecting a run does not change the displayed pipeline — behaviour is unchanged', async () => {
    await renderPage()
    // Nothing selected yet, so the detail column shows its prompt.
    expect(screen.getByText(/select a pipeline run/i)).toBeInTheDocument()

    const select = screen.getByLabelText(/trigger a pipeline manually/i)
    fireEvent.change(select, { target: { value: 'run-2' } })

    await waitFor(() => {
      expect((select as HTMLSelectElement).value).toBe('run-2')
    })
    // Still the prompt: this control arms the trigger, it does not select a view.
    // If this ever starts failing, the control has quietly become a view selector
    // and the label above is now a lie.
    expect(screen.getByText(/select a pipeline run/i)).toBeInTheDocument()
  })
})
