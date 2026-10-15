import type {
  ActiveLiveRun,
  AgentPipelineRun,
  AgentStageResult,
  CancelPipelineResponse,
  PipelineActionConflict,
  PipelineTimeline,
  RetryPipelineResponse,
  RunSummary,
} from '@/types/agent'
import { getData, postData } from './http'

/** What `POST /agents/pipelines/trigger` answers: 202 queued, or 200 when one is already in progress. */
export interface TriggerPipelineResponse {
  message: string
  task_id?: string
  run_id: string
  pipeline_run_id?: string
}

const agentService = {
  listPipelines: (runId?: string, projectId?: string, status?: string, limit = 20) =>
    getData<AgentPipelineRun[]>('/api/v1/agents/pipelines', {
      params: { run_id: runId, project_id: projectId, status, limit },
    }),

  getPipeline: (pipelineId: string) =>
    getData<AgentPipelineRun>(`/api/v1/agents/pipelines/${pipelineId}`),

  getStages: (pipelineId: string) =>
    getData<AgentStageResult[]>(`/api/v1/agents/pipelines/${pipelineId}/stages`),

  getTimeline: (pipelineId: string) =>
    getData<PipelineTimeline>(`/api/v1/agents/pipelines/${pipelineId}/timeline`),

  /** Queue a pipeline for a run. `workflowId` is sent only when given (W04 AGT-04:
   *  "Start a new run" of a deep pipeline must ask for `deep`, not the default). */
  triggerPipeline: (testRunId: string, workflowId?: 'offline' | 'deep') =>
    postData<TriggerPipelineResponse, { test_run_id: string; workflow_id?: 'offline' | 'deep' }>(
      '/api/v1/agents/pipelines/trigger',
      workflowId ? { test_run_id: testRunId, workflow_id: workflowId } : { test_run_id: testRunId },
    ),

  /** W04 AGT-04: retry a failed (or degraded) run. The SERVER decides resume
   *  (same id, completed stages kept) or rerun (a new run, already queued).
   *  The caller renders the outcome and the 409 itself, so no global toast. */
  retryPipeline: (pipelineId: string) =>
    postData<RetryPipelineResponse>(
      `/api/v1/agents/pipelines/${encodeURIComponent(pipelineId)}/retry`,
      undefined,
      { suppressToast: true },
    ),

  /** W04 AGT-04: stop a run. Terminal now (pending/retry_wait) or asked to stop
   *  at the next stage boundary (running). The caller renders the 409 itself. */
  cancelPipeline: (pipelineId: string) =>
    postData<CancelPipelineResponse>(
      `/api/v1/agents/pipelines/${encodeURIComponent(pipelineId)}/cancel`,
      undefined,
      { suppressToast: true },
    ),

  /** Trigger the deep-investigation pipeline (failure clustering + flaky
   *  sentinel + test health + release risk) for an existing run. The
   *  endpoint enforces the Settings › AI deep toggle and rejects 'rules' mode. */
  triggerDeepPipeline: (testRunId: string) =>
    postData<
      { task_id: string; pipeline_run_id: string; message: string; run_id: string },
      { mode: 'deep' | 'offline' }
    >(
      `/api/v1/deep-investigate/${testRunId}`,
      { mode: 'deep' },
    ),

  /** Queue the agent pipeline for many runs in one round-trip. Server
   *  handles batching internally (25-at-a-time with a small inter-batch
   *  sleep). Replaces the client-side Promise.allSettled fan-out so a
   *  500-run trigger is one HTTP call rather than 500. */
  bulkTriggerPipelines: (runIds: string[], workflowType: 'offline' | 'deep' = 'offline') =>
    postData<
      { queued: number; not_found: number; workflow_type: string; not_found_ids: string[] },
      { run_ids: string[]; workflow_type: 'offline' | 'deep' }
    >(
      '/api/v1/agents/pipelines/bulk-trigger',
      { run_ids: runIds, workflow_type: workflowType },
    ),

  getActiveLiveRuns: () =>
    getData<{ active_runs: ActiveLiveRun[] }>('/api/v1/agents/active-runs'),

  getRunSummary: (runId: string) =>
    getData<RunSummary>(`/api/v1/agents/runs/${runId}/summary`),
}

export default agentService
export type {
  ActiveLiveRun,
  AgentPipelineRun,
  AgentStageResult,
  CancelPipelineResponse,
  PipelineActionConflict,
  PipelineTimeline,
  RetryPipelineResponse,
  RunSummary,
}
