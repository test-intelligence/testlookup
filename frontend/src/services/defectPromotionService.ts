import { api } from './api'
import type {
  DefectApprovalRequest,
  DefectApprovalResponse,
  DefectCandidateResponse,
  DefectPromotionRequest,
  DefectPromotionResponse,
  PendingDefectList,
} from '@/types/defect-promotion'

export const defectPromotionService = {
  async getCandidate(
    runId: string,
    clusterId: string,
  ): Promise<DefectCandidateResponse> {
    const { data } = await api.get<DefectCandidateResponse>(
      `/api/v1/deep-investigate/${runId}/clusters/${encodeURIComponent(clusterId)}/defect-candidate`,
    )
    return data
  },

  async promote(
    runId: string,
    clusterId: string,
    body: DefectPromotionRequest,
  ): Promise<DefectPromotionResponse> {
    const { data } = await api.post<DefectPromotionResponse>(
      `/api/v1/deep-investigate/${runId}/clusters/${encodeURIComponent(clusterId)}/promote`,
      body,
    )
    return data
  },

  async reviewDefect(
    defectId: string,
    body: DefectApprovalRequest,
  ): Promise<DefectApprovalResponse> {
    const { data } = await api.post<DefectApprovalResponse>(
      `/api/v1/deep-investigate/defects/${defectId}/review`,
      body,
    )
    return data
  },

  /**
   * One project's defects awaiting a QA lead's approval (W06 DEF-06). QA_LEAD
   * only on the server: callers ask only for a QA lead (`usePendingDefects`).
   * `total` is the count over the whole scope, not this page's length.
   */
  async listPendingDefects(projectId: string, page = 1, pageSize = 100): Promise<PendingDefectList> {
    const { data } = await api.get<PendingDefectList>(
      '/api/v1/deep-investigate/defects/pending-review',
      { params: { project_id: projectId, page, page_size: pageSize } },
    )
    return data
  },
}
