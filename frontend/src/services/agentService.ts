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
import type {
  AgenticRun,
  BulkJob,
  BulkRunFilter,
  BulkTriggerResult,
  PipelineCompare,
  PipelineReplay,
} from '@/types/agentOps'
import { getData, postData } from './http'

/** What `POST /agents/pipelines/trigger` answers: 202 queued, or 200 when one is already in progress. */
export interface TriggerPipelineResponse {
  message: string
  task_id?: string
  run_id: string
  pipeline_run_id?: string
  /** W14 AGT-08: the named workflow's ref; null when the project default resolves at task start. */
  workflow_ref?: string | null
  /** W14 AGT-08: `resolved_at_start` when no workflow was named. */
  workflow_source?: string | null
}

/** A bulk request's idempotency token: 16 random bytes as 32 lower-case hex
 *  characters (the server's `^[A-Za-z0-9_-]+$`, 8..64). `crypto.getRandomValues`,
 *  not `crypto.randomUUID`: the latter exists only in secure contexts and the
 *  homelab is served over plain HTTP. */
export function newClientToken(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
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
   *  "Start a new run" of a deep pipeline must ask for `deep`); without one the
   *  server runs the project default (W14 AGT-08). `workflowVersion` is sent
   *  only together with a `workflowId` (a published fork's version). */
  triggerPipeline: (testRunId: string, workflowId?: string, workflowVersion?: number) =>
    postData<TriggerPipelineResponse, { test_run_id: string; workflow_id?: string; workflow_version?: number }>(
      '/api/v1/agents/pipelines/trigger',
      {
        test_run_id: testRunId,
        ...(workflowId ? { workflow_id: workflowId } : {}),
        ...(workflowId && workflowVersion != null ? { workflow_version: workflowVersion } : {}),
      },
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
   *  500-run trigger is one HTTP call rather than 500. W14 AGT-12: the
   *  answer is a tracked job (`bulk_job_id`); `clientToken` makes a double
   *  click / a retried request queue the batch once, and is minted here when
   *  not given (the one-argument call shape stays valid). */
  bulkTriggerPipelines: (
    runIds: string[],
    workflowType: 'offline' | 'deep' = 'offline',
    clientToken: string = newClientToken(),
  ) =>
    postData<
      BulkTriggerResult,
      { run_ids: string[]; workflow_type: 'offline' | 'deep'; client_token: string }
    >(
      '/api/v1/agents/pipelines/bulk-trigger',
      { run_ids: runIds, workflow_type: workflowType, client_token: clientToken },
      { suppressToast: true },
    ),

  /** W14 AGT-12: re-trigger every run matching the Runs list's filter (resolved on the server, capped at 2,000). */
  bulkTriggerFiltered: (runFilter: BulkRunFilter, workflowType: 'offline' | 'deep', clientToken: string) =>
    postData<
      BulkTriggerResult,
      { run_filter: BulkRunFilter; workflow_type: 'offline' | 'deep'; client_token: string }
    >(
      '/api/v1/agents/pipelines/bulk-trigger',
      { run_filter: runFilter, workflow_type: workflowType, client_token: clientToken },
      { suppressToast: true },
    ),

  /** W14 AGT-12: a bulk job's request facts and its progress counted from the pipelines. */
  getBulkJob: (jobId: string) =>
    getData<BulkJob>(`/api/v1/agents/pipelines/bulk-jobs/${encodeURIComponent(jobId)}`),

  /** W14 AGT-12: stop the job's not-yet-started tasks (running pipelines keep their own Stop). */
  cancelBulkJob: (jobId: string) =>
    postData<BulkJob>(
      `/api/v1/agents/pipelines/bulk-jobs/${encodeURIComponent(jobId)}/cancel`,
      undefined,
      { suppressToast: true },
    ),

  /** W14 AGT-05: the pipeline's replay (audit) document. */
  getReplay: (pipelineId: string) =>
    getData<PipelineReplay>(`/api/v1/agents/pipelines/${encodeURIComponent(pipelineId)}/replay`),

  /** W14 AGT-06: the pipeline's task graph (the generic compute canvas). */
  getAgenticRuntime: (pipelineId: string) =>
    getData<AgenticRun>(`/api/v1/agents/pipelines/${encodeURIComponent(pipelineId)}/agentic-runtime`),

  /** W14 AGT-05: two pipelines of one project side by side, stage by stage. */
  comparePipelines: (a: string, b: string) =>
    getData<PipelineCompare>('/api/v1/agents/pipelines/compare', { params: { a, b } }),

  getActiveLiveRuns: () =>
    getData<{ active_runs: ActiveLiveRun[] }>('/api/v1/agents/active-runs'),

  getRunSummary: (runId: string) =>
    getData<RunSummary>(`/api/v1/agents/runs/${runId}/summary`),
}

export default agentService
export type {
  AgenticRun,
  BulkJob,
  BulkRunFilter,
  BulkTriggerResult,
  PipelineCompare,
  PipelineReplay,
}
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
