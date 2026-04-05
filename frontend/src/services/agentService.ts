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

  getActiveLiveRuns: () =>
    getData<{ active_runs: ActiveLiveRun[] }>('/api/v1/agents/active-runs'),

  getRunSummary: (runId: string) =>
    getData<RunSummary>(`/api/v1/agents/runs/${runId}/summary`),
}

export default agentService
export type { ActiveLiveRun, AgentPipelineRun, AgentStageResult, PipelineTimeline, RunSummary }
