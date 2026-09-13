// Human review gate (architecture E8, section 8). Mirrors the backend
// `routers/reviews.py` ReviewResponse and `schemas.ReviewBlock`.

export type ReviewState = 'pending_review' | 'accepted' | 'rejected' | 'superseded'
/** A report's review block adds `not_applicable`: the content was not AI-generated. */
export type ReviewBlockState = ReviewState | 'not_applicable'
export type ReviewStateFilter = ReviewState | 'all'

export const REVIEW_REASON_CODES = [
  'wrong_category',
  'unsupported_claim',
  'missing_evidence',
  'contradiction',
  'stale_data',
  'other',
] as const
export type ReviewReasonCode = (typeof REVIEW_REASON_CODES)[number]

export const REVIEW_REASON_LABEL: Record<ReviewReasonCode, string> = {
  wrong_category: 'Wrong category',
  unsupported_claim: 'Unsupported claim',
  missing_evidence: 'Missing evidence',
  contradiction: 'Contradiction',
  stale_data: 'Stale data',
  other: 'Other',
}

export const REVIEW_STATE_LABEL: Record<ReviewBlockState, string> = {
  pending_review: 'Awaiting review',
  accepted: 'Accepted',
  rejected: 'Rejected',
  superseded: 'Superseded',
  not_applicable: 'Not AI-generated',
}

/** A review request. Carries no reviewer identity (section 8.2). */
export interface Review {
  id: string
  project_id: string
  kind: string
  subject_type: string
  subject_id: string
  pipeline_run_id: string | null
  test_run_id: string | null
  workflow_type: string | null
  state: ReviewState
  reviewed: boolean
  reviewed_at: string | null
  reason_code: string | null
  notes: string | null
  evidence_bundle_sha256: string | null
  superseded_by: string | null
  created_at: string
  requires_human_review: boolean
  ai_disclaimer: string
  ai_disclaimer_version: string
}

export interface ReviewBlock {
  state: ReviewBlockState
  message: string
  review_id?: string | null
  reviewed_at?: string | null
}

/**
 * Fields a report response gains from the review envelope (E8.3). All optional:
 * a cached payload or an older API omits them.
 */
export interface ReviewEnvelopeFields {
  requires_human_review?: boolean
  review?: ReviewBlock | null
  ai_disclaimer?: string | null
  ai_disclaimer_version?: string | null
}
