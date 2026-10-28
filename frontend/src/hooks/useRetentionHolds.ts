import useSWR from 'swr'
import { listRetentionHolds } from '@/services/retentionHoldService'
import { appMutate } from '@/utils/swrCacheMutate'
import type { HoldStateFilter, RetentionHold, RetentionHoldList } from '@/types/retentionHolds'

/** One stable empty list, so a consumer's effect deps do not change every render. */
const NO_HOLDS: RetentionHold[] = []

/**
 * W22a ACT-05: the retention holds of one project, in one state.
 *
 * The key is `null` (no request) without a project, so a page that has not
 * chosen to show holds asks nothing. A page without `items` (an older server
 * or fixture) reads as no holds; `total` is `null` until a first answer — an
 * unknown count is not 0 — and `error` says when the read failed (absence of
 * holds is never inferred from a failed read).
 */
export function useRetentionHolds(projectId: string | null, state: HoldStateFilter = 'live') {
  const id = projectId
  const swr = useSWR<RetentionHoldList>(
    id ? [`/api/v1/projects/${id}/retention-holds`, state] : null,
    id ? () => listRetentionHolds(id, { state }) : null,
    { revalidateOnFocus: false },
  )
  const items = swr.data?.items
  const holds: RetentionHold[] = Array.isArray(items) ? items : NO_HOLDS
  return {
    holds,
    total: swr.data ? (typeof swr.data.total === 'number' ? swr.data.total : holds.length) : null,
    counts: swr.data?.counts ?? null,
    isLoading: swr.isLoading,
    error: swr.error,
    mutate: swr.mutate,
  }
}

/**
 * Revalidate every state of one project's hold list (the release strip, the
 * pack rows and the Holds card share it). `appMutate`: this app's SWR cache
 * lives in its own provider (see `utils/swrCacheMutate`).
 */
export function refreshRetentionHolds(projectId: string | null) {
  if (!projectId) return Promise.resolve(undefined)
  const path = `/api/v1/projects/${projectId}/retention-holds`
  return appMutate((key: unknown) => Array.isArray(key) && key[0] === path)
}
