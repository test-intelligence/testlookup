import { getData, postData } from './http'

/** ``GET /api/v1/projects/{id}/analyses/lookup`` (US-2.4). All fields are
 *  null when the test has never been AI-analysed — the UI renders an empty
 *  state, not an error. */
export interface AnalysisLookupResponse {
  analysis_id: string | null
  failure_category: string | null
  analyzed_at: string | null
}

export type FeedbackRating = 'correct' | 'incorrect' | 'partially_correct'

/** Body for ``POST /api/v1/feedback/{analysis_id}``. rating=incorrect +
 *  corrected_category overwrites the analysis category and feeds the
 *  training loop (see backend feedback_service.submit_feedback). */
export interface FeedbackPayload {
  rating: FeedbackRating
  corrected_category?: string
  corrected_root_cause?: string
  comment?: string
}

export interface FeedbackResponse {
  feedback_id: string
  message: string
}

export const aiFeedbackService = {
  lookupAnalysis: (projectId: string, fingerprint: string) =>
    getData<AnalysisLookupResponse>(
      `/api/v1/projects/${projectId}/analyses/lookup`,
      { params: { fingerprint } },
    ),

  submitFeedback: (analysisId: string, payload: FeedbackPayload) =>
    postData<FeedbackResponse>(`/api/v1/feedback/${analysisId}`, payload),
}
