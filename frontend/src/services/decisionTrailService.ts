import { getData } from './http'
import type { DecisionTrailResponse } from '@/types/decisionTrail'

export const decisionTrailService = {
  /** Fetch the AI decision trail for a run. */
  get: (runId: string) =>
    getData<DecisionTrailResponse>(`/api/v1/runs/${runId}/decision-trail`),
}
