import { getData, putData } from './http'
import type { ReviewGateWrite } from '@/types/reviewGate'

/**
 * W23c AGT-10: the review gate's enforcement date (Settings › AI › Review gate, ADMIN).
 *
 * - GET: any ADMIN credential (an admin API key too) — reads only; the answer is RAW, read through
 *   `parseReviewGate` by `hooks/useReviewGate`;
 * - PUT: an ADMIN signed in interactively (an API key is 403 `interactive_only`); a naive datetime is 422
 *   `naive_datetime`. It passes `suppressToast: true`: the page shows `detail.message` inline under the
 *   field, where the refusal happened.
 *
 * Every request path is written out in full in its call (the SPA wire-contract guard reads them).
 */

export function getReviewGate(): Promise<unknown> {
  return getData<unknown>('/api/v1/settings/review-gate')
}

export function putReviewGate(body: ReviewGateWrite): Promise<unknown> {
  return putData<unknown, ReviewGateWrite>('/api/v1/settings/review-gate', body, { suppressToast: true })
}
