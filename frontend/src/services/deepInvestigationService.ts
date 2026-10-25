import type { DeepFinding, FailureCluster, ReleaseDecision } from '@/types/deep-investigation'
import type {
  DeepEstimate,
  DeepEstimateQuery,
  DeepHistory,
  DeepPreScan,
  DeepRunRequest,
  TriggerDeepResult,
} from '@/types/agentOps'
import { getData, postData } from './http'

/** WF-1: Real pipeline execution status for a workflow type. */
export interface PipelineStatus {
  pipeline_run_id: string | null
  workflow_type: string
  status: 'never_run' | 'pending' | 'running' | 'retry_wait' | 'completed' | 'passed' | 'failed'
  /** Four-value public projection (E7.5); optional for an older payload. */
  public_status?: string
  /** A completed run whose stages degraded (the old `partial`). */
  degraded?: boolean
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
  /** Queue a deep (or offline) investigation. W14 AGT-07: an optional run
   *  configuration (failure subset, tighter cap / tier / evidence sources) rides
   *  in the same body; without one the body is `{ mode }` exactly as before. */
  triggerDeep: (runId: string, mode: 'deep' | 'offline' = 'deep', config?: DeepRunRequest) =>
    postData<TriggerDeepResult>(`/api/v1/deep-investigate/${runId}`, config ? { mode, ...config } : { mode }),

  /** W14 AGT-07: the run's failing results grouped by error signature (an offline read). */
  getPreScan: (runId: string, limit = 10) =>
    getData<DeepPreScan>(`/api/v1/deep-investigate/${runId}/pre-scan`, { params: { limit } }),

  /** W14 AGT-07: the project's deep-investigated runs, newest first, paged. */
  getHistory: (projectId: string, page = 1, size = 10) =>
    getData<DeepHistory>('/api/v1/deep-investigate/history', {
      params: { project_id: projectId, page, size },
    }),

  /** W14 AGT-07: the measured time / cost estimate and the server's own deep gate.
   *  `evidence_sources` repeats its key (api.ts `paramsSerializer: { indexes: null }`). */
  getEstimate: (runId: string, q: DeepEstimateQuery = {}) =>
    getData<DeepEstimate>(`/api/v1/deep-investigate/${runId}/estimate`, { params: q }),

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
export type { DeepEstimate, DeepEstimateQuery, DeepHistory, DeepPreScan, DeepRunRequest, TriggerDeepResult }
