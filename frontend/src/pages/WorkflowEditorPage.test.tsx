import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import WorkflowEditorPage, { buildWorkflowGraph } from './WorkflowEditorPage'
import {
  evaluateWorkflow,
  forkWorkflow,
  listWorkflows,
  publishWorkflow,
  updateWorkflow,
  validateWorkflow,
} from '@/services/workflowService'
import type { WorkflowBody, WorkflowItem } from '@/types/workflowDefinition'

const permissionState = vi.hoisted(() => ({ isQaLead: true }))

vi.mock('@/services/workflowService', () => ({
  evaluateWorkflow: vi.fn(),
  forkWorkflow: vi.fn(),
  listWorkflows: vi.fn(),
  publishWorkflow: vi.fn(),
  updateWorkflow: vi.fn(),
  validateWorkflow: vi.fn(),
}))

vi.mock('@/hooks/usePermissions', () => ({
  usePermissions: () => ({
    isQaLead: permissionState.isQaLead,
    canAccessManagement: permissionState.isQaLead,
  }),
}))

vi.mock('@/store/projectStore', () => {
  const state = {
    activeProjectId: 'project-1',
    projects: [],
    setActiveProject: vi.fn(),
  }
  return {
    ALL_PROJECTS_ID: 'all',
    useProjectStore: (selector: (value: typeof state) => unknown) => selector(state),
  }
})

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}))

const definition: WorkflowBody = {
  workflow_id: 'wf.fast',
  name: 'Fast triage',
  description: 'Quick analysis',
  base: 'offline',
  steps: [
    { id: 'ingest', agent_id: 'agent.ingestion.v1', tools: [], reviews: [] },
    { id: 'summary', agent_id: 'agent.summary.v1', tools: [], reviews: [] },
  ],
  edges: [{ from: 'ingest', to: 'summary' }],
  loops: [],
  retry_policy: { max_attempts: 5, base_seconds: 30, cap_seconds: 600 },
  review_policy: 'human_required',
  deadline_seconds: 1500,
}

function item(overrides: Partial<WorkflowItem>): WorkflowItem {
  const workflowId = overrides.workflow_id ?? definition.workflow_id
  const name = overrides.name ?? definition.name
  return {
    id: `${workflowId}-1`,
    workflow_id: workflowId,
    version: 1,
    project_id: workflowId === 'offline' ? null : 'project-1',
    name,
    description: overrides.description ?? definition.description ?? null,
    base: overrides.base ?? definition.base,
    definition: {
      ...definition,
      workflow_id: workflowId,
      base: overrides.base ?? definition.base,
      project_id: workflowId === 'offline' ? null : 'project-1',
      version: 1,
    },
    status: overrides.status ?? 'draft',
    published_at: null,
    eval_verdict: overrides.eval_verdict ?? null,
    eval_coverage: overrides.eval_coverage ?? null,
    eval_gate_run_id: null,
    evaluated_at: null,
    eval_regression_accepted: false,
    eval_regression_reason: null,
    eval_regression_accepted_at: null,
    read_only: overrides.read_only ?? false,
    built_in: overrides.built_in ?? false,
    ...overrides,
  }
}

const builtIn = item({
  id: 'offline', workflow_id: 'offline', name: 'Offline workflow', base: 'offline',
  status: 'published', read_only: true, built_in: true,
})
const custom = item({ eval_verdict: 'fail', eval_coverage: 0.8 })

function renderPage() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <MemoryRouter><WorkflowEditorPage /></MemoryRouter>
    </SWRConfig>,
  )
}

describe('WorkflowEditorPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    permissionState.isQaLead = true
    vi.mocked(listWorkflows).mockResolvedValue([builtIn, custom])
    vi.mocked(updateWorkflow).mockResolvedValue(custom)
    vi.mocked(validateWorkflow).mockResolvedValue({
      valid: true,
      workflow_id: custom.workflow_id,
      version: custom.version,
      errors: [],
      validation_scope: 'semantic',
      compiler_validation: 'passed',
    })
    vi.mocked(forkWorkflow).mockResolvedValue(item({ workflow_id: 'wf.offline_copy', name: 'Offline copy' }))
    vi.mocked(evaluateWorkflow).mockResolvedValue({
      verdict: 'pass', status: 'pass', reason: 'ok', workflow_id: custom.workflow_id,
      version: 1, sample_count: 20, measured_steps: 40, expected_steps: 40,
      coverage: 1, regressions: [],
    })
    vi.mocked(publishWorkflow).mockResolvedValue({ ...custom, status: 'published', read_only: true })
  })

  it('lists coverage, protects built-ins, forks them, and previews their graph', async () => {
    renderPage()

    expect(await screen.findByRole('heading', { name: 'Workflow editor' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Fast triage/i }))
    expect(screen.getByTestId('workflow-eval-coverage')).toHaveTextContent('80% coverage')
    fireEvent.click(screen.getByRole('button', { name: /Offline workflow/i }))
    expect(screen.getByText('Built-in definitions are immutable. Fork this version to customize it.')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Workflow definition JSON' })).toHaveAttribute('readonly')
    expect(screen.getByRole('img', { name: /workflow graph with 2 steps and 1 edges/i })).toBeInTheDocument()
    expect(screen.getAllByTestId('workflow-preview-edge')).toHaveLength(1)

    fireEvent.click(screen.getByRole('button', { name: /fork workflow/i }))
    fireEvent.change(screen.getByLabelText('New workflow ID'), { target: { value: 'wf.offline_copy' } })
    fireEvent.change(screen.getByLabelText('Fork name'), { target: { value: 'Offline copy' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create fork' }))

    await waitFor(() => expect(forkWorkflow).toHaveBeenCalledWith(
      'project-1', 'offline',
      { workflow_id: 'wf.offline_copy', name: 'Offline copy', description: 'Quick analysis' },
      1,
    ))
  })

  it('edits drafts, validates before publishing, evaluates, and carries a regression reason', async () => {
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /Fast triage/i }))

    const editor = screen.getByRole('textbox', { name: 'Workflow definition JSON' })
    const edited = { ...definition, name: 'Fast triage updated' }
    fireEvent.change(editor, { target: { value: JSON.stringify(edited, null, 2) } })
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }))

    await waitFor(() => expect(updateWorkflow).toHaveBeenCalledWith('project-1', 'wf.fast', edited))
    expect(validateWorkflow).toHaveBeenCalledWith('project-1', 'wf.fast', 1)

    fireEvent.click(screen.getByRole('button', { name: 'Run evaluation' }))
    await waitFor(() => expect(evaluateWorkflow).toHaveBeenCalledWith('project-1', 'wf.fast', 1))

    fireEvent.click(screen.getByRole('checkbox', { name: /accept the measured regression/i }))
    fireEvent.change(screen.getByLabelText('Regression acceptance reason'), {
      target: { value: 'Reviewed against the release corpus' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Publish' }))

    await waitFor(() => expect(publishWorkflow).toHaveBeenCalledWith('project-1', 'wf.fast', {
      accept_regression: true,
      reason: 'Reviewed against the release corpus',
    }))
  })

  it('refuses publication when semantic validation fails', async () => {
    vi.mocked(validateWorkflow).mockResolvedValue({
      valid: false,
      workflow_id: custom.workflow_id,
      version: 1,
      errors: ['summary depends on a missing step'],
      validation_scope: 'semantic',
      compiler_validation: 'failed',
    })
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /Fast triage/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Publish' }))

    expect(await screen.findByText('Semantic validation failed')).toBeInTheDocument()
    expect(screen.getByText('summary depends on a missing step')).toBeInTheDocument()
    expect(publishWorkflow).not.toHaveBeenCalled()
  })

  it('renders a data-unavailable state when workflow loading fails', async () => {
    vi.mocked(listWorkflows).mockRejectedValue(new Error('network unavailable'))
    renderPage()

    expect(await screen.findByTestId('workflow-data-unavailable')).toBeInTheDocument()
    expect(screen.queryByText('No workflow definitions are available.')).not.toBeInTheDocument()
  })

  it('keeps workflow mutations unavailable to non-QA-leads', async () => {
    permissionState.isQaLead = false
    renderPage()
    fireEvent.click(await screen.findByRole('button', { name: /Fast triage/i }))

    expect(screen.getByRole('textbox', { name: 'Workflow definition JSON' })).toHaveAttribute('readonly')
    expect(screen.queryByRole('button', { name: /fork workflow/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Run evaluation' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Publish' })).not.toBeInTheDocument()
  })
})

describe('buildWorkflowGraph', () => {
  it('expands fan-in sources and keeps loop edges visible', () => {
    const graph = buildWorkflowGraph({
      ...definition,
      steps: [
        ...definition.steps,
        { id: 'review', agent_id: 'agent.reviewer.v1', tools: [], reviews: ['summary'] },
      ],
      edges: [{ from: ['ingest', 'summary'], to: 'review', join: 'all' }],
      loops: [{ from: 'review', to: 'summary', when: { op: 'else' }, max_iterations: 2 }],
    })
    expect(graph.edges).toEqual(expect.arrayContaining([
      expect.objectContaining({ from: 'ingest', to: 'review', loop: false }),
      expect.objectContaining({ from: 'summary', to: 'review', loop: false }),
      expect.objectContaining({ from: 'review', to: 'summary', loop: true }),
    ]))
  })
})
