/**
 * W14 AGT-08 in the workflow editor (contract §2.3 test 7): "Use for new
 * {base} runs" for a published fork (QA lead), the read-only fact below that
 * role, nothing for a draft, the model-stages cost warning, and Delete draft
 * asked in the page (InlineConfirm).
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import WorkflowEditorPage from './WorkflowEditorPage'
import {
  clearWorkflowDefault,
  deleteWorkflow,
  getWorkflowDefaults,
  listWorkflows,
  setWorkflowDefault,
} from '@/services/workflowService'
import type { WorkflowItem } from '@/types/workflowDefinition'

const permissionState = vi.hoisted(() => ({ isQaLead: true }))

vi.mock('@/services/workflowService', () => ({
  evaluateWorkflow: vi.fn(), forkWorkflow: vi.fn(), listWorkflows: vi.fn(), publishWorkflow: vi.fn(),
  updateWorkflow: vi.fn(), validateWorkflow: vi.fn(), getWorkflowDefaults: vi.fn(), setWorkflowDefault: vi.fn(),
  clearWorkflowDefault: vi.fn(), deleteWorkflow: vi.fn(),
}))
vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({ isQaLead: permissionState.isQaLead, canAccessManagement: permissionState.isQaLead }),
}))
vi.mock('@/store/projectStore', () => {
  const state = { activeProjectId: 'project-1', projects: [], setActiveProject: vi.fn() }
  return { ALL_PROJECTS_ID: 'all', useProjectStore: (selector: (value: typeof state) => unknown) => selector(state) }
})
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))

function item(workflow_id: string, version: number, over: Partial<WorkflowItem> = {}): WorkflowItem {
  return {
    id: `${workflow_id}-${version}`, workflow_id, version, project_id: 'project-1', name: over.name ?? workflow_id,
    description: null, base: 'offline',
    definition: {
      workflow_id, project_id: 'project-1', version, base: over.base ?? 'offline',
      steps: [{ id: 'ingestion', agent_id: 'agent.ingestion.v1', tools: [], reviews: [] }], edges: [], loops: [],
      retry_policy: { max_attempts: 5, base_seconds: 30, cap_seconds: 600 }, review_policy: 'human_required', deadline_seconds: 1500,
    },
    definition_sha256: 'a'.repeat(64), status: 'published', published_at: '2026-10-01T00:00:00Z', eval_verdict: 'pass',
    eval_coverage: 1, eval_gate_run_id: null, evaluated_at: null, eval_regression_accepted: false, eval_regression_reason: null,
    eval_regression_accepted_at: null, read_only: false, built_in: false, ...over,
  }
}

const FORK = item('wf.fast', 1, { name: 'Fast triage' })
const DRAFT = item('wf.fast', 2, { name: 'Fast triage', status: 'draft', published_at: null })
const BUILTIN = item('offline', 1, { name: 'Offline workflow', project_id: null, built_in: true, read_only: true })

function defaults(offline: Record<string, unknown> = {}) {
  return {
    project_id: 'project-1',
    defaults: [
      { base: 'offline', source: 'builtin', workflow_id: 'offline', state: 'ok', resolved_ref: 'offline@1', model_stages: [], ...offline },
      { base: 'deep', source: 'builtin', workflow_id: 'deep', state: 'ok', resolved_ref: 'deep@1', model_stages: [] },
    ],
  }
}
const FORK_IS_DEFAULT = defaults({
  source: 'project_default', workflow_id: 'wf.fast', resolved_ref: 'wf.fast@1',
  model_stages: ['root_cause_analysis', 'summary', 'triage', 'gap_detection'],
})

function renderPage() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter><WorkflowEditorPage /></MemoryRouter>
    </SWRConfig>,
  )
}
const theSwitch = () => screen.findByRole('switch', { name: 'Use for new offline runs' })

beforeEach(() => {
  vi.clearAllMocks()
  permissionState.isQaLead = true
  vi.mocked(listWorkflows).mockResolvedValue([FORK, BUILTIN])
  vi.mocked(getWorkflowDefaults).mockResolvedValue(defaults() as never)
  vi.mocked(setWorkflowDefault).mockResolvedValue({} as never)
  vi.mocked(clearWorkflowDefault).mockResolvedValue(undefined)
  vi.mocked(deleteWorkflow).mockResolvedValue(undefined)
})

describe('WorkflowEditorPage — Use for new runs', () => {
  it('a QA lead turns a published fork ON: an unpinned default for its base', async () => {
    renderPage()
    const toggle = await theSwitch()
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    expect(getWorkflowDefaults).toHaveBeenCalledWith('project-1')
    fireEvent.click(toggle)
    await waitFor(() => expect(setWorkflowDefault).toHaveBeenCalledWith('project-1', 'offline', { workflow_id: 'wf.fast' }))
    // Re-read after the write.
    await waitFor(() => expect(getWorkflowDefaults).toHaveBeenCalledTimes(2))
  })

  it('ON shows the cost of the model stages; OFF clears the default', async () => {
    vi.mocked(getWorkflowDefaults).mockResolvedValue(FORK_IS_DEFAULT as never)
    const { container } = renderPage()
    const toggle = await theSwitch()
    expect(toggle).toHaveAttribute('aria-checked', 'true')
    expect(container.querySelector('[data-model-stages]')).toHaveTextContent(
      'Every new run calls a model in 4 stages: root_cause_analysis, summary, triage…',
    )
    fireEvent.click(toggle)
    await waitFor(() => expect(clearWorkflowDefault).toHaveBeenCalledWith('project-1', 'offline'))
  })

  it('shows the server\'s refusal beside the switch', async () => {
    vi.mocked(setWorkflowDefault).mockRejectedValue({ response: { status: 422, data: { detail: 'Only a published workflow can be the default: publish wf.fast first.' } } })
    renderPage()
    fireEvent.click(await theSwitch())
    expect(await screen.findByText('Only a published workflow can be the default: publish wf.fast first.')).toBeInTheDocument()
  })

  it('no switch for a draft', async () => {
    vi.mocked(listWorkflows).mockResolvedValue([DRAFT, FORK, BUILTIN])
    renderPage()
    await screen.findByRole('button', { name: 'Delete draft' })
    expect(screen.queryByRole('switch')).toBeNull()
  })

  it('below QA lead: no switch, the read-only fact when the fork is the default', async () => {
    permissionState.isQaLead = false
    vi.mocked(getWorkflowDefaults).mockResolvedValue(FORK_IS_DEFAULT as never)
    renderPage()
    expect(await screen.findByText('Used for new offline runs')).toBeInTheDocument()
    expect(screen.queryByRole('switch')).toBeNull()
  })

  it('a built-in says it runs unless a fork is the default', async () => {
    vi.mocked(listWorkflows).mockResolvedValue([BUILTIN, FORK])
    vi.mocked(getWorkflowDefaults).mockResolvedValue(FORK_IS_DEFAULT as never)
    renderPage()
    expect(await screen.findByText('Used for new offline runs unless a fork is the project default (now: wf.fast@1)')).toBeInTheDocument()
  })
})

describe('WorkflowEditorPage — Delete draft', () => {
  it('asks in the page, then deletes the latest draft and re-reads the list', async () => {
    vi.mocked(listWorkflows).mockResolvedValue([DRAFT, FORK, BUILTIN])
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Delete draft' }))
    const confirm = screen.getByTestId('workflow-delete-draft-confirm')
    expect(confirm).toHaveTextContent('Delete draft version 2 of wf.fast? Published versions are kept.')
    expect(deleteWorkflow).not.toHaveBeenCalled()
    fireEvent.click(confirm.querySelector('[data-inline-confirm-action]') as HTMLElement)
    await waitFor(() => expect(deleteWorkflow).toHaveBeenCalledWith('project-1', 'wf.fast'))
    await waitFor(() => expect(listWorkflows).toHaveBeenCalledTimes(2))
  })

  it('renders a 409 sentence', async () => {
    vi.mocked(listWorkflows).mockResolvedValue([DRAFT, FORK, BUILTIN])
    vi.mocked(deleteWorkflow).mockRejectedValue({ response: { status: 409, data: { detail: 'Only the latest draft version can be deleted' } } })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: 'Delete draft' }))
    fireEvent.click(screen.getByTestId('workflow-delete-draft-confirm').querySelector('[data-inline-confirm-action]') as HTMLElement)
    expect(await screen.findByText('Only the latest draft version can be deleted')).toBeInTheDocument()
  })

  it('no Delete draft for a published version or below QA lead', async () => {
    renderPage()
    await theSwitch()
    expect(screen.queryByRole('button', { name: 'Delete draft' })).toBeNull()
  })
})
