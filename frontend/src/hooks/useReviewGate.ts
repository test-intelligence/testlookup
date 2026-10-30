/**
 * W23c AGT-10: the review gate's enforcement date (Settings › AI › Review gate, ADMIN only).
 *
 * One SWR key, `['review-gate']`; no key — no request — when `enabled` is false (a non-admin sees the
 * page's "Only administrators …" sentence and never a 403 toast). The answer goes through
 * `parseReviewGate`: a malformed one comes back as `data: null` WITH an `error` (the page says "Could not
 * load the review gate."), never a crash. No polling and no focus revalidation: the date changes on a
 * click, and the would-refuse count is a 30-day aggregate.
 *
 * `save(enforcedFrom | null)` PUTs (an ISO instant WITH an offset, or null to clear) and puts the PUT's own
 * answer (the same `ReviewGateRead`) into the cache. It rethrows a refusal, so the page renders the
 * 403 / 422 `detail.message` inline.
 */
import { useCallback } from 'react'
import useSWR from 'swr'
import { getReviewGate, putReviewGate } from '@/services/reviewGateService'
import { parseReviewGate, type ReviewGateRead } from '@/types/reviewGate'

/** The malformed-answer error (the request itself succeeded). */
export const MALFORMED_REVIEW_GATE = 'The review gate answer could not be read.'

export const REVIEW_GATE_KEY = ['review-gate'] as const

async function fetchReviewGate(): Promise<ReviewGateRead> {
  const parsed = parseReviewGate(await getReviewGate())
  if (parsed === null) throw new Error(MALFORMED_REVIEW_GATE)
  return parsed
}

export interface ReviewGateState {
  /** `null` while loading, on a failed read, or for a malformed answer. */
  data: ReviewGateRead | null
  /** Set on a failed read AND on a malformed answer. */
  error: unknown
  isLoading: boolean
  mutate: () => Promise<unknown>
  /** PUT the date (`null` clears it); resolves with the new state, rejects with the refusal. */
  save: (enforcedFrom: string | null) => Promise<ReviewGateRead>
}

export function useReviewGate(enabled = true): ReviewGateState {
  const { data, error, isLoading, mutate } = useSWR<ReviewGateRead>(
    enabled ? REVIEW_GATE_KEY : null,
    fetchReviewGate,
    { revalidateOnFocus: false },
  )
  const save = useCallback(
    async (enforcedFrom: string | null): Promise<ReviewGateRead> => {
      const parsed = parseReviewGate(await putReviewGate({ enforced_from: enforcedFrom }))
      if (parsed === null) {
        // The write landed but its answer is unreadable: read the state again.
        await mutate()
        throw new Error(MALFORMED_REVIEW_GATE)
      }
      await mutate(parsed, { revalidate: false })
      return parsed
    },
    [mutate],
  )
  return { data: data ?? null, error, isLoading, mutate: () => mutate(), save }
}
