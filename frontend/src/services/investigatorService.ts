import type {
  CancelInvestigationResponse,
  InvestigationDetail,
  InvestigationListResponse,
  StartInvestigationResponse,
} from '@/types/investigator'
import { getData, postData } from './http'

/**
 * Investigator agent (AI-1) — hypothesis-loop deep investigations.
 * Endpoints are the pinned Wave-B contract; the backend half implements the
 * same paths verbatim.
 *
 * Error semantics on start:
 *   409 — an investigation is already running for the run (attach to it).
 *   403 — the investigator policy is disabled for the project; the response
 *         `detail` carries an actionable message to surface as-is.
 */
export const investigatorService = {
  /** POST /runs/{run_id}/investigations → 202 {investigation_id}. */
  startInvestigation: (runId: string) =>
    postData<StartInvestigationResponse>(`/api/v1/runs/${runId}/investigations`),

  getInvestigation: (investigationId: string) =>
    getData<InvestigationDetail>(`/api/v1/investigations/${investigationId}`),

  /** POST .../cancel → 202 {"status":"cancelling"}. */
  cancelInvestigation: (investigationId: string) =>
    postData<CancelInvestigationResponse>(`/api/v1/investigations/${investigationId}/cancel`),

  listInvestigations: (projectId: string, limit = 20, offset = 0) =>
    getData<InvestigationListResponse>(`/api/v1/projects/${projectId}/investigations`, {
      params: { limit, offset },
    }),
}

export default investigatorService
