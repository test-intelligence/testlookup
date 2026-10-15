/**
 * W04 AGT-04: Retry (resume) and Stop on the `/agents` pipeline rows.
 *
 * The routes existed (`POST /agents/pipelines/{id}/retry` and `/cancel`, both
 * QA_ENGINEER) and the rows showed "retrying n/m" and "stopping" chips with
 * no control. Pinned here:
 *  - who sees what: Retry on a failed or degraded-completed row (the mirror
 *    of the backend's `is_resumable`), never on a passed or clean completed
 *    one; Stop on an in-progress row (not once a stop was asked for); nothing
 *    for a role below QA engineer;
 *  - the server decides resume vs rerun and the note says which; a 409 is
 *    shown in the server's words (review rejection in its own) and, when the
 *    server offers it, "Start a new run" queues the SAME workflow type;
 *  - Stop is a two-step inline confirm, then the note; a second click is
 *    impossible while the request is in flight;
 *  - the row is a button with its actions BESIDE it, never inside it.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import AgentStatusPage from './AgentStatusPage'

const { mockProjectState, permissions, agent, toastApi, mutate } = vi.hoisted(() => ({
  mockProjectState: {
    activeProjectId: 'proj-1',
    activeProject: { id: 'proj-1', name: 'Project One' },
  },
  permissions: { isQaEngineer: true },
  agent: {
    retryPipeline: vi.fn(),
    cancelPipeline: vi.fn(),
    triggerPipeline: vi.fn(),
  },
  toastApi: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
  mutate: vi.fn(),
}))

vi.mock('@/hooks/useAgentRuns', () => ({
  usePipelines: vi.fn(),
  usePipelineStages: vi.fn(() => ({ data: [], isLoading: false })),
  usePipelineTimeline: vi.fn(() => ({ data: undefined, isLoading: false })),
  useRunSummary: vi.fn(() => ({ data: undefined, isLoading: false, error: undefined })),
  useActiveLiveRuns: vi.fn(() => ({ data: [] })),
}))
vi.mock('@/hooks/useAIConfig', () => ({
  useAIConfig: vi.fn(() => ({ data: { analysis_mode: 'auto', deep_investigation_enabled: true } })),
}))
vi.mock('@/hooks/useRuns', () => ({
  useRuns: vi.fn(() => ({ data: { items: [] }, isLoading: false })),
}))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: vi.fn(() => permissions) }))
vi.mock('@/services/agentService', () => ({ default: agent }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) => selector(mockProjectState)),
}))
vi.mock('react-hot-toast', () => ({ default: toastApi }))

function pipeline(id: string, fields: Record<string, unknown>) {
  return {
    id,
    test_run_id: `run-${id}`,
    workflow_type: 'offline',
    status: 'failed',
    started_at: '2026-09-12T10:00:00Z',
    completed_at: '2026-09-12T10:05:00Z',
    error: null,
    created_at: '2026-09-12T10:00:00Z',
    execution_metadata: {},
    provenance_metadata: null,
    build_number: `b-${id}`,
    run_seq: 7,
    suite_name: null,
    attempt: 1,
    max_attempts: 5,
    ...fields,
  }
}

async function renderWith(pipelines: unknown[]) {
  const { usePipelines } = await import('@/hooks/useAgentRuns')
  ;(usePipelines as ReturnType<typeof vi.fn>).mockReturnValue({ data: pipelines, isLoading: false, mutate })
  return render(
    <MemoryRouter initialEntries={['/agents']}>
      <Routes>
        <Route path="/agents" element={<AgentStatusPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

const rowOf = (id: string) => document.querySelector(`[data-testid="pipeline-row"][data-pipeline-id="${id}"]`) as HTMLElement

/** An axios-shaped rejection. */
const httpError = (status: number, detail: unknown) =>
  Object.assign(new Error(`Request failed with status code ${status}`), { response: { status, data: { detail } } })

let consoleError: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  vi.clearAllMocks()
  permissions.isQaEngineer = true
  consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  consoleError.mockRestore()
})

describe('which rows offer Retry and Stop', () => {
  it('Retry on a failed and a degraded-completed row; Stop on in-progress rows; nothing on passed or clean completed', async () => {
    await renderWith([
      pipeline('failed', { status: 'failed' }),
      pipeline('degraded', { status: 'completed', execution_metadata: { stage_quality: 'degraded' } }),
      pipeline('passed', { status: 'passed' }),
      pipeline('completed', { status: 'completed' }),
      pipeline('running', { status: 'running' }),
      pipeline('waiting', { status: 'retry_wait', attempt: 2 }),
      pipeline('stopping', { status: 'running', cancel_requested: true }),
    ])
    const has = (id: string, testId: string) => within(rowOf(id)).queryByTestId(testId) !== null

    expect(has('failed', 'pipeline-retry')).toBe(true)
    expect(has('degraded', 'pipeline-retry')).toBe(true)
    for (const id of ['passed', 'completed', 'running', 'waiting', 'stopping']) {
      expect(has(id, 'pipeline-retry'), `${id}: no Retry`).toBe(false)
    }
    // In progress (incl. waiting for its automatic retry): Stop, not Retry.
    expect(has('running', 'pipeline-stop')).toBe(true)
    expect(has('waiting', 'pipeline-stop')).toBe(true)
    // A stop already asked for: the chip says so, no second Stop.
    expect(has('stopping', 'pipeline-stop')).toBe(false)
    expect(within(rowOf('stopping')).getByTestId('pipeline-stopping')).toHaveTextContent('stopping')
    for (const id of ['failed', 'degraded', 'passed', 'completed']) {
      expect(has(id, 'pipeline-stop'), `${id}: no Stop`).toBe(false)
    }
  })

  it('a role below QA engineer sees no actions at all', async () => {
    permissions.isQaEngineer = false
    await renderWith([pipeline('failed', { status: 'failed' }), pipeline('running', { status: 'running' })])
    expect(screen.queryByTestId('pipeline-row-actions')).toBeNull()
    expect(screen.queryByTestId('pipeline-retry')).toBeNull()
    expect(screen.queryByTestId('pipeline-stop')).toBeNull()
  })

  it('the actions sit beside the row button, never inside it (no button in a button)', async () => {
    const { container } = await renderWith([pipeline('failed', { status: 'failed' }), pipeline('running', { status: 'running' })])
    expect(container.querySelectorAll('button button')).toHaveLength(0)
    const row = rowOf('failed')
    const select = within(row).getByRole('button', { name: /offline pipeline/i })
    expect(select.contains(within(row).getByTestId('pipeline-retry'))).toBe(false)
    const nesting = consoleError.mock.calls.filter((args: unknown[]) => String(args[0]).includes('validateDOMNesting') || String(args[0]).includes('cannot be a descendant'))
    expect(nesting).toEqual([])
    // The row's own control still selects it.
    expect(select).toHaveAttribute('aria-pressed', 'false')
  })
})

describe('Retry', () => {
  it('resume: one POST, the attempt note, and the list is revalidated', async () => {
    agent.retryPipeline.mockResolvedValue({
      mode: 'resume', pipeline_run_id: 'failed', rerun_of: null, attempt: 2, max_attempts: 5,
      reason: 'config_unchanged', status: 'failed', public_status: 'failed', links: {},
    })
    await renderWith([pipeline('failed', { status: 'failed' })])
    fireEvent.click(screen.getByTestId('pipeline-retry'))
    expect(await screen.findByTestId('pipeline-action-note')).toHaveTextContent('Resumed (attempt 2/5) — keeps the completed stages.')
    expect(agent.retryPipeline).toHaveBeenCalledTimes(1)
    expect(agent.retryPipeline).toHaveBeenCalledWith('failed')
    expect(mutate).toHaveBeenCalled()
    expect(screen.queryByTestId('pipeline-start-new-run')).toBeNull()
  })

  it('rerun: the server started a new run because the configuration changed (or there was no plan to resume)', async () => {
    agent.retryPipeline.mockResolvedValueOnce({
      mode: 'rerun', pipeline_run_id: null, rerun_of: 'failed', attempt: 1, max_attempts: 5,
      reason: 'config_changed', status: 'pending', public_status: 'in_progress', links: {},
    })
    await renderWith([pipeline('failed', { status: 'failed' })])
    fireEvent.click(screen.getByTestId('pipeline-retry'))
    expect(await screen.findByTestId('pipeline-action-note')).toHaveTextContent(
      'Started a new run — the analysis configuration changed since this run started.',
    )

    agent.retryPipeline.mockResolvedValueOnce({
      mode: 'rerun', pipeline_run_id: null, rerun_of: 'failed', attempt: 1, max_attempts: 5,
      reason: 'no_frozen_plan', status: 'pending', public_status: 'in_progress', links: {},
    })
    fireEvent.click(screen.getByTestId('pipeline-retry'))
    await waitFor(() => expect(screen.getByTestId('pipeline-action-note')).toHaveTextContent(
      'Started a new run — this run has no saved plan to resume.',
    ))
    // The page never triggers a run itself: the server already queued it.
    expect(agent.triggerPipeline).not.toHaveBeenCalled()
  })

  it('409 review rejected: its own line and "Start a new run", which queues a DEEP run for a deep pipeline', async () => {
    agent.retryPipeline.mockRejectedValue(httpError(409, {
      message: 'Pipeline was rejected in review; start a new run instead',
      reason: 'review_rejected',
      links: { rerun: '/api/v1/agents/pipelines/trigger' },
    }))
    agent.triggerPipeline.mockResolvedValue({ message: 'Pipeline queued', task_id: 't-1', run_id: 'run-deep' })
    await renderWith([pipeline('deep', { status: 'failed', workflow_type: 'deep', test_run_id: 'run-deep' })])
    fireEvent.click(screen.getByTestId('pipeline-retry'))
    const note = await screen.findByTestId('pipeline-action-note')
    expect(note).toHaveTextContent('Rejected in review — start a new run.')
    expect(note).toHaveAttribute('data-tone', 'error')

    fireEvent.click(screen.getByTestId('pipeline-start-new-run'))
    await waitFor(() => expect(agent.triggerPipeline).toHaveBeenCalledWith('run-deep', 'deep'))
    await waitFor(() => expect(toastApi.success).toHaveBeenCalledWith('Pipeline queued — it will appear in the list shortly.'))
    await waitFor(() => expect(screen.queryByTestId('pipeline-start-new-run')).toBeNull())
  })

  it('409 at the attempt ceiling: the server\'s message, and "Start a new run" queues an offline run for an offline pipeline', async () => {
    agent.retryPipeline.mockRejectedValue(httpError(409, {
      message: 'Pipeline reached its attempt ceiling (5/5). Start a new run instead.',
      attempt: 5, max_attempts: 5,
      links: { rerun: '/api/v1/agents/pipelines/trigger' },
    }))
    agent.triggerPipeline.mockResolvedValue({ message: 'Pipeline queued', task_id: 't-1', run_id: 'run-failed' })
    await renderWith([pipeline('failed', { status: 'failed', attempt: 5 })])
    fireEvent.click(screen.getByTestId('pipeline-retry'))
    expect(await screen.findByTestId('pipeline-action-note')).toHaveTextContent(
      'Pipeline reached its attempt ceiling (5/5). Start a new run instead.',
    )
    fireEvent.click(screen.getByTestId('pipeline-start-new-run'))
    await waitFor(() => expect(agent.triggerPipeline).toHaveBeenCalledWith('run-failed', 'offline'))
  })

  it('a 409 without links.rerun offers no new run; a non-409 refusal (the deep gate\'s 422) is shown in the server\'s words', async () => {
    agent.retryPipeline.mockRejectedValueOnce(httpError(409, {
      message: 'Another pipeline for this run is already in progress',
      pipeline_run_id: 'other',
    }))
    await renderWith([pipeline('failed', { status: 'failed', workflow_type: 'deep' })])
    fireEvent.click(screen.getByTestId('pipeline-retry'))
    expect(await screen.findByTestId('pipeline-action-note')).toHaveTextContent('Another pipeline for this run is already in progress')
    expect(screen.queryByTestId('pipeline-start-new-run')).toBeNull()

    agent.retryPipeline.mockRejectedValueOnce(httpError(422, 'Deep investigation is disabled in Settings › AI. Ask an administrator to enable it.'))
    fireEvent.click(screen.getByTestId('pipeline-retry'))
    await waitFor(() => expect(screen.getByTestId('pipeline-action-note')).toHaveTextContent(
      'Deep investigation is disabled in Settings › AI. Ask an administrator to enable it.',
    ))
  })

  it('is disabled while the request is in flight (one click, one POST)', async () => {
    let resolve: (v: unknown) => void = () => {}
    agent.retryPipeline.mockReturnValue(new Promise((r) => { resolve = r }))
    await renderWith([pipeline('failed', { status: 'failed' })])
    const button = screen.getByTestId('pipeline-retry')
    fireEvent.click(button)
    expect(button).toBeDisabled()
    expect(button).toHaveTextContent('Retrying…')
    fireEvent.click(button)
    expect(agent.retryPipeline).toHaveBeenCalledTimes(1)
    await act(async () => {
      resolve({ mode: 'resume', pipeline_run_id: 'failed', rerun_of: null, attempt: 2, max_attempts: 5, reason: 'config_unchanged', status: 'failed', public_status: 'failed', links: {} })
    })
    expect(screen.getByTestId('pipeline-retry')).toBeEnabled()
  })
})

describe('Stop', () => {
  it('asks first, then stops: a running run is asked to stop at its next stage boundary', async () => {
    let resolve: (v: unknown) => void = () => {}
    agent.cancelPipeline.mockReturnValue(new Promise((r) => { resolve = r }))
    await renderWith([pipeline('running', { status: 'running' })])
    fireEvent.click(screen.getByTestId('pipeline-stop'))
    // Nothing sent yet: an inline confirm, not window.confirm.
    expect(agent.cancelPipeline).not.toHaveBeenCalled()
    const confirm = screen.getByTestId('pipeline-stop-confirm')
    expect(confirm).toHaveTextContent('Stop this run?')

    const yes = screen.getByTestId('pipeline-stop-confirm-yes')
    fireEvent.click(yes)
    expect(yes).toBeDisabled()
    fireEvent.click(yes)
    expect(agent.cancelPipeline).toHaveBeenCalledTimes(1)
    expect(agent.cancelPipeline).toHaveBeenCalledWith('running')
    await act(async () => {
      resolve({ pipeline_run_id: 'running', accepted: true, terminal: false, status: 'running', public_status: 'in_progress', reason: 'stop requested' })
    })
    expect(screen.getByTestId('pipeline-action-note')).toHaveTextContent('Stop requested — the run ends at its next stage boundary.')
    expect(mutate).toHaveBeenCalled()
    expect(screen.queryByTestId('pipeline-stop-confirm')).toBeNull()
  })

  it('a run with no live worker stops now; "Keep running" sends nothing; a 409 shows the server\'s message', async () => {
    agent.cancelPipeline.mockResolvedValueOnce({ pipeline_run_id: 'pending', accepted: true, terminal: true, status: 'failed', public_status: 'failed', reason: 'cancelled' })
    await renderWith([pipeline('pending', { status: 'pending' })])

    fireEvent.click(screen.getByTestId('pipeline-stop'))
    fireEvent.click(screen.getByTestId('pipeline-stop-confirm-no'))
    expect(agent.cancelPipeline).not.toHaveBeenCalled()
    expect(screen.getByTestId('pipeline-stop')).toBeInTheDocument()

    fireEvent.click(screen.getByTestId('pipeline-stop'))
    fireEvent.click(screen.getByTestId('pipeline-stop-confirm-yes'))
    expect(await screen.findByTestId('pipeline-action-note')).toHaveTextContent(/^Stopped\.$/)

    agent.cancelPipeline.mockRejectedValueOnce(httpError(409, {
      message: 'Pipeline has already finished', accepted: false, terminal: true, status: 'completed',
    }))
    fireEvent.click(screen.getByTestId('pipeline-stop'))
    fireEvent.click(screen.getByTestId('pipeline-stop-confirm-yes'))
    await waitFor(() => expect(screen.getByTestId('pipeline-action-note')).toHaveTextContent('Pipeline has already finished'))
    expect(screen.getByTestId('pipeline-action-note')).toHaveAttribute('data-tone', 'error')
  })
})
