/**
 * W23b FAIL-12: the narrowed range of ONE failing test of ONE run (the run's bisect read, `getBisect`).
 *
 * Keyed by the request URL (`bisectKey`); no key — no request — without a run or a fingerprint. Mount =
 * open: the block that calls this renders only while its section is open, so nothing is read on page load.
 * The answer goes through `parseBisect`: a malformed one comes back as `data: null` WITH an `error`, so the
 * block says "Could not load the narrowed range." and never crashes the section.
 *
 * No polling, no revalidation on focus and no automatic retry of a failed read (a 404 is answered once):
 * `retry()` is the explicit "Retry" of a `timed_out` compare. A NEW module on purpose:
 * `hooks/useCommitAttribution` is `vi.mock`ed by five tests whose factories would lack a new export.
 */
import { useMemo } from 'react'
import useSWR from 'swr'
import { getBisect } from '@/services/bisectService'
import { parseBisect, type BisectNarrowing } from '@/types/bisect'

/** The malformed-answer error (the request itself succeeded). */
export const MALFORMED_BISECT = 'The narrowed range could not be read.'

const OPTS = { revalidateOnFocus: false, shouldRetryOnError: false } as const

/** The SWR key (= the request URL) of one narrowing read, or `null` (no request) when either id is missing. */
export function bisectKey(runId: string | null, fingerprint: string | null): string | null {
  if (!runId || !fingerprint) return null
  return `/api/v1/runs/${runId}/bisect?fingerprint=${encodeURIComponent(fingerprint)}`
}

export interface BisectNarrowingState {
  /** `null` while loading, on a failed read, or for a malformed answer. */
  data: BisectNarrowing | null
  /** Set on a failed read AND on a malformed answer. */
  error: unknown
  isLoading: boolean
  /** Read again (the "Retry" of a `timed_out` compare). */
  retry: () => Promise<unknown>
}

export function useBisectNarrowing(runId: string | null, fingerprint: string | null): BisectNarrowingState {
  const key = bisectKey(runId, fingerprint)
  const swr = useSWR<unknown>(key, runId && fingerprint ? () => getBisect(runId, fingerprint) : null, OPTS)
  const raw = swr.data
  const parsed = useMemo(() => (raw === undefined ? null : parseBisect(raw)), [raw])
  const malformed = raw !== undefined && parsed === null
  return {
    data: parsed,
    error: swr.error ?? (malformed ? new Error(MALFORMED_BISECT) : undefined),
    isLoading: Boolean(swr.isLoading),
    retry: () => swr.mutate(),
  }
}
