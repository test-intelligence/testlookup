import { api } from './api'
import type {
  DefectApprovalRequest,
  DefectApprovalResponse,
  DefectCandidateResponse,
  DefectPromotionRequest,
  DefectPromotionResponse,
  PendingDefect,
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

  async listPendingDefects(): Promise<PendingDefect[]> {
    const { data } = await api.get<PendingDefect[]>(
      '/api/v1/deep-investigate/defects/pending-review',
    )
    return data
  },
}
