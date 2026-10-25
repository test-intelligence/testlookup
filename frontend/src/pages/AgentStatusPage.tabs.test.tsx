/**
 * W14 AGT-05 / AGT-06 / AGT-08 on `/agents` (contract §2.1 test 7, §2.2 test 4):
 * the Graph · Audit · Compare strip inside "Agent stages" under `?ptab=`, the
 * Graph switch (the hand canvas ONLY for `offline@1` whose stages all map),
 * the workflow line, and the Choose workflow chevron. Every new read is a
 * `useAgentOps` hook (stubbed here) asked only once its view is shown.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { useEffect } from 'react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AgentStatusPage from './AgentStatusPage'
import RunEvidenceTab from './run/RunEvidenceTab'
import { useAgenticRuntime, usePipelineCompare, usePipelineReplay, useWorkflowChoices } from '@/hooks/useAgentOps'
import { usePipelineStages, usePipelineTimeline, usePipelines } from '@/hooks/useAgentRuns'
import { idleSwr, REPLAY_FIXTURE, RUNTIME_INVESTIGATION_FIXTURE } from '@/test/agentOpsMocks'

const { mockProjectState, permissions } = vi.hoisted(() => ({
  mockProjectState: { activeProjectId: 'proj-1', activeProject: { id: 'proj-1', name: 'Project One' } },
  permissions: { isQaEngineer: true },
}))

vi.mock('@/hooks/useAgentOps', async () => (await import('@/test/agentOpsMocks')).agentOpsHookMocks())
vi.mock('@/hooks/useAgentRuns', () => ({
  usePipelines: vi.fn(),
  usePipelineStages: vi.fn(),
  usePipelineTimeline: vi.fn(),
  useRunSummary: vi.fn(() => ({ data: undefined, isLoading: false, error: undefined })),
  useActiveLiveRuns: vi.fn(() => ({ data: [] })),
}))
vi.mock('@/hooks/useAIConfig', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/hooks/useAIConfig')>()),
  useAIConfig: vi.fn(() => ({ data: { analysis_mode: 'auto', deep_investigation_enabled: true } })),
}))
vi.mock('@/hooks/useRuns', () => ({ useRuns: vi.fn(() => ({ data: { items: [] }, isLoading: false })) }))
vi.mock('@/hooks/usePermissions', () => ({ usePermissions: vi.fn(() => permissions) }))
vi.mock('@/services/agentService', () => ({ default: { triggerPipeline: vi.fn() } }))
vi.mock('@/store/projectStore', () => ({
  ALL_PROJECTS_ID: '__ALL__',
  useProjectStore: vi.fn((selector: (state: typeof mockProjectState) => unknown) => selector(mockProjectState)),
}))
vi.mock('react-hot-toast', () => ({ default: { success: vi.fn(), error: vi.fn() } }))
// The Evidence tab's decision report has its own reads; this file is about the strip below it.
vi.mock('./RunIntelligencePage', () => ({ RunDecisionReport: () => null }))

const stage = (stage_name: string, status = 'completed') => ({
  stage_name, status, started_at: '2026-10-20T10:00:00Z', completed_at: '2026-10-20T10:00:30Z',
  result_data: null, error: null, skipped_reason: null, execution_path: null, fallback_used: null,
  input_tokens: null, output_tokens: null, total_tokens: null, llm_calls_count: null, cost_usd: null,
  error_category: null, confidence_score: null, evidence_count: null, route_rationale: null,
})
const OFFLINE_STAGES = ['ingestion', 'anomaly_detection', 'root_cause_analysis', 'summary', 'triage'].map(n => stage(n))
const DEEP_STAGES = [
  'ingestion', 'anomaly_detection', 'failure_clustering', 'cluster_investigation_dispatch', 'root_cause_analysis',
  'cluster_investigation_join', 'summary', 'triage', 'contract_validation', 'log_intelligence', 'regression_watchman',
  'change_ownership', 'defect_commander', 'gap_detection', 'report_refinement', 'flaky_sentinel', 'test_health',
  'release_risk', 'decision_report', 'decision_report_critic',
].map(n => stage(n))

function pipeline(id: string, over: Record<string, unknown> = {}) {
  return {
    id, test_run_id: 'run-1', workflow_type: 'offline', status: 'completed', public_status: 'completed',
    started_at: '2026-10-20T10:00:00Z', completed_at: '2026-10-20T10:01:00Z', error: null,
    created_at: '2026-10-20T10:00:00Z', execution_metadata: null, provenance_metadata: null, ...over,
  }
}

function timeline(over: Record<string, unknown> = {}) {
  return {
    schema_version: 2, pipeline_run_id: 'pipe-2', workflow_type: 'offline', status: 'completed', public_status: 'completed',
    started_at: null, completed_at: null, duration_seconds: null, summary: {}, cost_summary: {}, stages: [], events: [], alerts: [],
    ...over,
  }
}

let location = ''
function LocationProbe() {
  const { pathname, search } = useLocation()
  useEffect(() => { location = `${pathname}${search}` })
  return null
}

function setup({ stages = OFFLINE_STAGES, tl = timeline(), pipelines = [pipeline('pipe-2', { created_at: '2026-10-20T11:00:00Z' }), pipeline('pipe-1')] }: {
  stages?: unknown[]; tl?: unknown; pipelines?: unknown[]
} = {}) {
  vi.mocked(usePipelines).mockReturnValue({ data: pipelines, isLoading: false, mutate: vi.fn() } as never)
  vi.mocked(usePipelineStages).mockReturnValue({ data: stages, isLoading: false } as never)
  vi.mocked(usePipelineTimeline).mockReturnValue({ data: tl } as never)
}

function renderAgents(entry = '/agents?run=run-1') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes><Route path="/agents" element={<><AgentStatusPage /><LocationProbe /></>} /></Routes>
    </MemoryRouter>,
  )
}

const openStages = () => fireEvent.click(screen.getByRole('button', { name: /^Agent stages/ }))
const strip = () => screen.getByRole('tablist', { name: 'Pipeline views' })
const viewTab = (name: RegExp) => within(strip()).getByRole('tab', { name })

beforeEach(() => {
  vi.clearAllMocks()
  permissions.isQaEngineer = true
  location = ''
  for (const hook of [usePipelineReplay, useAgenticRuntime, usePipelineCompare, useWorkflowChoices]) {
    vi.mocked(hook).mockReturnValue(idleSwr() as never)
  }
})

describe('/agents — Graph · Audit · Compare (AGT-05)', () => {
  it('asks for nothing new on load; the strip appears inside Agent stages, Graph first', () => {
    setup()
    renderAgents()
    expect(screen.queryByRole('tablist', { name: 'Pipeline views' })).toBeNull()
    for (const hook of [usePipelineReplay, useAgenticRuntime, usePipelineCompare, useWorkflowChoices]) {
      expect(vi.mocked(hook)).not.toHaveBeenCalled()
    }
    openStages()
    expect(within(strip()).getAllByRole('tab').map(t => t.getAttribute('data-tab'))).toEqual(['graph', 'audit', 'compare'])
    expect(viewTab(/^Graph$/)).toHaveAttribute('aria-selected', 'true')
  })

  it('?ptab=audit opens Audit, and the replay is asked only while Audit is shown', () => {
    setup()
    vi.mocked(usePipelineReplay).mockReturnValue(idleSwr({ data: REPLAY_FIXTURE }) as never)
    renderAgents('/agents?run=run-1&ptab=audit')
    openStages()
    expect(viewTab(/^Audit/)).toHaveAttribute('aria-selected', 'true')
    expect(vi.mocked(usePipelineReplay)).toHaveBeenCalledWith('pipe-2', false)
    expect(screen.getByRole('region', { name: 'Audit gaps' })).toBeInTheDocument()
    vi.mocked(usePipelineReplay).mockClear()
    fireEvent.click(viewTab(/^Graph$/))
    expect(vi.mocked(usePipelineReplay)).not.toHaveBeenCalled()
    // Graph is the clean URL: the key goes, the run stays.
    expect(new URLSearchParams(location.split('?')[1]).get('ptab')).toBeNull()
    expect(new URLSearchParams(location.split('?')[1]).get('run')).toBe('run-1')
  })

  it('the Audit badge comes from the timeline already on the page', () => {
    setup({ tl: timeline({ replay_integrity: { replayable: false, audit_gaps: { missing_start_events: ['a', 'b'], missing_terminal_events: [], missing_replay_checksums: [], missing_checkpoints: [], missing_final_state_checksum: true } } }) })
    renderAgents()
    openStages()
    expect(viewTab(/^Audit/)).toHaveTextContent('Audit · 3 gaps')
  })

  it('Compare offers the run\'s other pipeline; with none it is disabled and says why', () => {
    setup()
    renderAgents()
    openStages()
    fireEvent.click(viewTab(/^Compare$/))
    expect(new URLSearchParams(location.split('?')[1]).get('ptab')).toBe('compare')
    expect(within(screen.getByLabelText('Compare with')).getAllByRole('option').map(o => o.getAttribute('value'))).toEqual(['pipe-1'])
    expect(vi.mocked(usePipelineCompare)).toHaveBeenLastCalledWith('pipe-2', 'pipe-1')
  })

  it('a run with one pipeline: Compare is disabled with its reason', () => {
    setup({ pipelines: [pipeline('pipe-2')] })
    renderAgents()
    openStages()
    expect(viewTab(/^Compare$/)).toBeDisabled()
    expect(viewTab(/^Compare$/)).toHaveAttribute('title', 'No other pipeline of this run to compare with')
  })

  it('shows the workflow line in the AI Report header row', () => {
    setup({ tl: timeline({ workflow_ref: 'wf.my-fork@1', workflow_source: 'project_default' }) })
    const { container } = renderAgents()
    expect(container.querySelector('[data-workflow-line]')).toHaveTextContent('Workflow wf.my-fork@1 · project default')
  })

  it('a legacy timeline (no workflow_ref) has no workflow line', () => {
    setup()
    const { container } = renderAgents()
    expect(container.querySelector('[data-workflow-line]')).toBeNull()
  })
})

describe('/agents — the Graph switch (AGT-06)', () => {
  it('offline@1 whose stages all map: the hand canvas, and NO runtime read', () => {
    setup({ tl: timeline({ workflow_ref: 'offline@1' }) })
    const { container } = renderAgents()
    openStages()
    expect(container.querySelector('[data-generic-graph]')).toBeNull()
    expect(screen.getAllByText('Anomaly Detection').length).toBeGreaterThan(0)
    expect(vi.mocked(useAgenticRuntime)).not.toHaveBeenCalled()
  })

  it('the built-in deep pipeline (11 stages without a hand node) is drawn from the runtime', () => {
    setup({ stages: DEEP_STAGES, tl: timeline({ workflow_type: 'deep', workflow_ref: 'deep@1' }) })
    vi.mocked(useAgenticRuntime).mockReturnValue(idleSwr({ data: RUNTIME_INVESTIGATION_FIXTURE }) as never)
    const { container } = renderAgents()
    openStages()
    expect(vi.mocked(useAgenticRuntime)).toHaveBeenCalledWith('pipe-2', false)
    expect(container.querySelector('[data-generic-graph]')).not.toBeNull()
    expect(container.querySelectorAll('[data-stage-id]')).toHaveLength(7)
  })

  it('live@1 (two stages that both map) is generic too: the hand canvas is offline@1 only', () => {
    setup({ stages: ['ingestion', 'summary'].map(n => stage(n, 'running')), tl: timeline({ workflow_type: 'live', workflow_ref: 'live@1', status: 'running', public_status: 'in_progress' }) })
    renderAgents()
    openStages()
    expect(vi.mocked(useAgenticRuntime)).toHaveBeenCalledWith('pipe-2', true)
  })

  it('a runtime that cannot be read: the sentence above the plain stage list', () => {
    setup({ stages: DEEP_STAGES, tl: timeline({ workflow_ref: 'deep@1' }) })
    vi.mocked(useAgenticRuntime).mockReturnValue(idleSwr({ error: new Error('500') }) as never)
    renderAgents()
    openStages()
    expect(screen.getByText('Could not load the pipeline graph.')).toBeInTheDocument()
    expect(screen.getByText('decision_report_critic')).toBeInTheDocument()
  })
})

describe('/agents — Choose workflow (AGT-08)', () => {
  it('Trigger pipeline keeps one combobox; the chevron opens the in-page picker with the second', () => {
    setup()
    renderAgents()
    expect(screen.getAllByRole('combobox')).toHaveLength(1)
    expect(screen.getByRole('button', { name: /trigger pipeline/i })).toHaveAttribute('title', "Queue the project's default pipeline for the run picked here")
    expect(vi.mocked(useWorkflowChoices)).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Choose workflow' }))
    expect(screen.getByRole('region', { name: 'Trigger a pipeline' })).toBeInTheDocument()
    expect(screen.getAllByRole('combobox')).toHaveLength(2)
    expect(vi.mocked(useWorkflowChoices)).toHaveBeenCalledWith('proj-1', true)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(screen.getAllByRole('combobox')).toHaveLength(1)
  })

  it('no chevron below QA engineer', () => {
    permissions.isQaEngineer = false
    setup()
    renderAgents()
    expect(screen.queryByRole('button', { name: 'Choose workflow' })).toBeNull()
  })
})

describe('Run page Evidence tab — the same strip, ?tab= untouched', () => {
  it('mounts Graph · Audit · Compare and writes ptab beside tab=evidence', () => {
    setup()
    render(
      <MemoryRouter initialEntries={['/runs/run-1?tab=evidence']}>
        <Routes><Route path="/runs/:runId" element={<><RunEvidenceTab runId="run-1" /><LocationProbe /></>} /></Routes>
      </MemoryRouter>,
    )
    openStages()
    fireEvent.click(viewTab(/^Audit/))
    const params = new URLSearchParams(location.split('?')[1])
    expect(params.get('tab')).toBe('evidence')
    expect(params.get('ptab')).toBe('audit')
    expect(vi.mocked(usePipelineReplay)).toHaveBeenCalledWith('pipe-2', false)
  })
})
