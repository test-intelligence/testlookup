/**
 * W14 AGT-08: the workflow picker (contract §2.3 test 6). Driven through the
 * REAL `useWorkflowChoices` hook over mocked services, so "each fork's newest
 * PUBLISHED version, no drafts" is the code the page runs.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { SWRConfig } from 'swr'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import WorkflowPicker from './WorkflowPicker'
import agentService from '@/services/agentService'
import { getWorkflowDefaults, listWorkflows } from '@/services/workflowService'
import type { WorkflowItem } from '@/types/workflowDefinition'

const { aiMode, toastApi } = vi.hoisted(() => ({
  aiMode: { current: { analysis_mode: 'auto', deep_investigation_enabled: true } as Record<string, unknown> | undefined },
  toastApi: { success: vi.fn(), error: vi.fn() },
}))

vi.mock('@/services/workflowService', () => ({ listWorkflows: vi.fn(), getWorkflowDefaults: vi.fn() }))
vi.mock('@/services/agentService', () => ({ default: { triggerPipeline: vi.fn() } }))
vi.mock('@/hooks/useAIConfig', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/hooks/useAIConfig')>()),
  useAIConfig: vi.fn(() => ({ data: aiMode.current })),
}))
vi.mock('react-hot-toast', () => ({ default: toastApi }))

function wf(workflow_id: string, version: number, over: Partial<WorkflowItem> = {}): WorkflowItem {
  return {
    id: `${workflow_id}-${version}`, workflow_id, version, project_id: 'proj-1', name: over.name ?? workflow_id,
    description: null, base: 'offline', definition: {} as WorkflowItem['definition'], definition_sha256: 'a'.repeat(64),
    status: 'published', published_at: `2026-10-0${version}T00:00:00Z`, eval_verdict: null, eval_coverage: null,
    eval_gate_run_id: null, evaluated_at: null, eval_regression_accepted: false, eval_regression_reason: null,
    eval_regression_accepted_at: null, read_only: false, built_in: false, ...over,
  }
}

const ITEMS: WorkflowItem[] = [
  wf('offline', 1, { project_id: null, built_in: true, name: 'Offline workflow' }),
  wf('deep', 1, { project_id: null, built_in: true, base: 'deep', name: 'Deep workflow' }),
  wf('wf.fast', 1, { name: 'Fast triage' }),
  wf('wf.fast', 3, { name: 'Fast triage' }),
  wf('wf.fast', 4, { name: 'Fast triage', status: 'draft', published_at: null }),
  wf('wf.draftonly', 1, { name: 'Draft only', status: 'draft', published_at: null }),
  wf('wf.deepfork', 2, { name: 'Deep fork', base: 'deep' }),
]

function defaults(offline: Record<string, unknown> = {}) {
  return {
    project_id: 'proj-1',
    defaults: [
      { base: 'offline', source: 'builtin', workflow_id: 'offline', state: 'ok', resolved_ref: 'offline@1', model_stages: [], ...offline },
      { base: 'deep', source: 'builtin', workflow_id: 'deep', state: 'ok', resolved_ref: 'deep@1', model_stages: [] },
    ],
  }
}

function renderPicker(onClose = vi.fn()) {
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <WorkflowPicker projectId="proj-1" runId="run-9" onClose={onClose} />
    </SWRConfig>,
  )
  return onClose
}

const optionLabels = () => within(screen.getByRole('combobox', { name: 'Workflow' })).getAllByRole('option').map(o => o.textContent)

beforeEach(() => {
  vi.clearAllMocks()
  aiMode.current = { analysis_mode: 'auto', deep_investigation_enabled: true }
  vi.mocked(listWorkflows).mockResolvedValue(ITEMS)
  vi.mocked(getWorkflowDefaults).mockResolvedValue(defaults() as never)
  vi.mocked(agentService.triggerPipeline).mockResolvedValue({} as never)
})

describe('WorkflowPicker', () => {
  it('lists the default first, the built-ins, then each fork\'s newest PUBLISHED version — no drafts', async () => {
    renderPicker()
    await waitFor(() => expect(optionLabels()).toHaveLength(5))
    expect(optionLabels()).toEqual([
      'Project default — built-in offline',
      'Built-in offline',
      'Built-in deep',
      // The latest published first (v3 on Oct 3, the deep fork's v2 on Oct 2).
      'Fast triage · wf.fast@3',
      'Deep fork · wf.deepfork@2',
    ])
    expect(listWorkflows).toHaveBeenCalledWith('proj-1')
    expect(getWorkflowDefaults).toHaveBeenCalledWith('proj-1')
  })

  it('names a usable project default by its resolved ref', async () => {
    vi.mocked(getWorkflowDefaults).mockResolvedValue(defaults({ source: 'project_default', workflow_id: 'wf.fast', resolved_ref: 'wf.fast@3' }) as never)
    renderPicker()
    await waitFor(() => expect(optionLabels()[0]).toBe('Project default — wf.fast@3'))
  })

  it('hides deep (built-in and deep-based forks) while Settings › AI does not allow it', async () => {
    aiMode.current = { analysis_mode: 'rules', deep_investigation_enabled: true }
    renderPicker()
    await waitFor(() => expect(optionLabels()).toHaveLength(3))
    expect(optionLabels()).toEqual(['Project default — built-in offline', 'Built-in offline', 'Fast triage · wf.fast@3'])
  })

  it('Queue sends the run ALONE for the project default (the one-click body)', async () => {
    const onClose = renderPicker()
    await waitFor(() => expect(optionLabels()).toHaveLength(5))
    fireEvent.click(screen.getByRole('button', { name: 'Queue pipeline' }))
    await waitFor(() => expect(agentService.triggerPipeline).toHaveBeenCalledTimes(1))
    expect(vi.mocked(agentService.triggerPipeline).mock.calls[0]).toEqual(['run-9'])
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(toastApi.success).toHaveBeenCalledWith('Pipeline queued — it will appear in the list shortly.')
  })

  it('Queue sends the fork id and its version', async () => {
    renderPicker()
    await waitFor(() => expect(optionLabels()).toHaveLength(5))
    fireEvent.change(screen.getByRole('combobox', { name: 'Workflow' }), { target: { value: 'fork:wf.fast@3' } })
    fireEvent.click(screen.getByRole('button', { name: 'Queue pipeline' }))
    await waitFor(() => expect(agentService.triggerPipeline).toHaveBeenCalledWith('run-9', 'wf.fast', 3))
  })

  it('a built-in is named without a version', async () => {
    renderPicker()
    await waitFor(() => expect(optionLabels()).toHaveLength(5))
    fireEvent.change(screen.getByRole('combobox', { name: 'Workflow' }), { target: { value: 'builtin:deep' } })
    fireEvent.click(screen.getByRole('button', { name: 'Queue pipeline' }))
    await waitFor(() => expect(agentService.triggerPipeline).toHaveBeenCalledTimes(1))
    expect(vi.mocked(agentService.triggerPipeline).mock.calls[0]).toEqual(['run-9', 'deep'])
  })

  it('shows the server\'s 409 sentence in the strip, and stays open', async () => {
    vi.mocked(agentService.triggerPipeline).mockRejectedValue({ response: { status: 409, data: { detail: 'Workflow definition is not published' } } })
    const onClose = renderPicker()
    await waitFor(() => expect(optionLabels()).toHaveLength(5))
    fireEvent.click(screen.getByRole('button', { name: 'Queue pipeline' }))
    expect(await screen.findByText('Workflow definition is not published')).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('region', { name: 'Trigger a pipeline' })).toBeInTheDocument()
  })
})
