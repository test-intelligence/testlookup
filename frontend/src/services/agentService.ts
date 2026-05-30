import type { ActiveLiveRun, AgentPipelineRun, AgentStageResult, PipelineTimeline, RunSummary } from '@/types/agent'
import { getData, postData } from './http'

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

  triggerPipeline: (testRunId: string) =>
    postData<{ message: string; task_id: string; run_id: string }, { test_run_id: string }>(
      '/api/v1/agents/pipelines/trigger',
      { test_run_id: testRunId },
    ),

  /** Trigger the deep-investigation pipeline (failure clustering + flaky
   *  sentinel + test health + release risk) for an existing run. The
   *  endpoint enforces DEEP_INVESTIGATION_ENABLED and rejects 'rules' mode. */
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
export type { ActiveLiveRun, AgentPipelineRun, AgentStageResult, PipelineTimeline, RunSummary }
