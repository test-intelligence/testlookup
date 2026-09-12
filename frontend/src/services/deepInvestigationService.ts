import type { DeepFinding, FailureCluster, ReleaseDecision } from '@/types/deep-investigation'
import { getData, postData } from './http'

/** WF-1: Real pipeline execution status for a workflow type. */
export interface PipelineStatus {
  pipeline_run_id: string | null
  workflow_type: string
  status: 'never_run' | 'pending' | 'running' | 'retry_wait' | 'completed' | 'passed' | 'failed'
  started_at: string | null
  completed_at: string | null
  error: string | null
  stage_summary: {
    completed: number
    failed: number
    skipped: number
    pending: number
  }
}

export const deepInvestigationService = {
  triggerDeep: (runId: string, mode: 'deep' | 'offline' = 'deep') =>
    postData(`/api/v1/deep-investigate/${runId}`, { mode }),

  /** WF-1: Get real pipeline execution status for the run. */
  getPipelineStatus: (runId: string, workflowType: 'deep' | 'offline' = 'deep') =>
    getData<PipelineStatus>(`/api/v1/agents/runs/${runId}/pipeline-status`, { params: { workflow_type: workflowType } }),

  getClusters: (runId: string) =>
    getData<FailureCluster[]>(`/api/v1/deep-investigate/${runId}/clusters`),

  getFindings: (runId: string) =>
    getData<DeepFinding[]>(`/api/v1/deep-investigate/${runId}/findings`),

  getReleaseDecision: (runId: string) =>
    getData<ReleaseDecision>(`/api/v1/release-readiness/${runId}`),

  overrideRelease: (runId: string, override_recommendation: string, reason: string) =>
    postData<ReleaseDecision, { override_recommendation: string; reason: string }>(`/api/v1/release-readiness/${runId}/override`, {
      override_recommendation,
      reason,
    }),
}
export type { DeepFinding, FailureCluster, ReleaseDecision }
