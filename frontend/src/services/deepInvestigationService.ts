import type { DeepFinding, FailureCluster, ReleaseDecision } from '@/types/deep-investigation'
import { getData, postData } from './http'

export const deepInvestigationService = {
  triggerDeep: (runId: string, mode: 'deep' | 'offline' = 'deep') =>
    postData(`/api/v1/deep-investigate/${runId}`, { mode }),

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
