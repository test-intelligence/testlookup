import { getData, postData } from './http'
import type { Review, ReviewReasonCode, ReviewStateFilter } from '@/types/review'

/**
 * Human review gate API (architecture E8.2).
 *
 * Accept and reject are QA_LEAD+, refused to API keys, and enforce separation
 * of duties server-side; the shared API interceptor surfaces those refusals.
 */
export const reviewService = {
  /** A project's review queue, newest first. `all` sends no state filter. */
  list: (projectId: string, state: ReviewStateFilter = 'pending_review', limit = 200) =>
    getData<Review[]>(`/api/v1/projects/${encodeURIComponent(projectId)}/reviews`, {
      params: { limit, ...(state !== 'all' ? { state } : {}) },
    }),

  /** Accept an AI report. Its pipeline run becomes `passed`. */
  accept: (reviewId: string, notes?: string) =>
    postData<Review, { notes: string } | undefined>(
      `/api/v1/reviews/${encodeURIComponent(reviewId)}/accept`,
      notes ? { notes } : undefined,
    ),

  /** Reject an AI report with a reason code. Its pipeline run becomes `failed`. */
  reject: (reviewId: string, reasonCode: ReviewReasonCode, notes?: string) =>
    postData<Review, { reason_code: ReviewReasonCode; notes?: string }>(
      `/api/v1/reviews/${encodeURIComponent(reviewId)}/reject`,
      { reason_code: reasonCode, ...(notes ? { notes } : {}) },
    ),
}
