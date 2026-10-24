import { useCallback } from 'react'
import useSWR from 'swr'
import { searchService } from '@/services/searchService'
import type { IndexStatus } from '@/types/search'

/**
 * W12 SRCH-01 — the semantic index status for the /search page (the Mode
 * chips' availability, the verdict ribbon's freshness, the Searchable data
 * card's semantic line). One request on load, as the page's raw effect made
 * before; no poll, unless a caller passes the time a re-index it queued was
 * accepted (`queuedAt`): then every 10 s until `last_indexed_at` is later than
 * that time, for at most 10 minutes.
 *
 * A read that fails is `unknown` (never `healthy`): absence is not health.
 */
export const REINDEX_POLL_MS = 10_000
export const REINDEX_POLL_LIMIT_MS = 10 * 60_000

export const UNKNOWN_INDEX_STATUS: IndexStatus = { status: 'unknown', document_count: 0, last_indexed_at: null }

export type ReindexProgress = 'pending' | 'done' | 'timeout'

/** Where a re-index queued at `queuedAt` stands, from the status and the clock; null when none is queued. */
export function reindexProgress(
  status: IndexStatus | null | undefined,
  queuedAt: number | null | undefined,
  now: number,
): ReindexProgress | null {
  if (queuedAt == null) return null
  const last = status?.last_indexed_at ? Date.parse(status.last_indexed_at) : Number.NaN
  if (Number.isFinite(last) && last > queuedAt) return 'done'
  return now - queuedAt >= REINDEX_POLL_LIMIT_MS ? 'timeout' : 'pending'
}

export function searchIndexStatusKey(projectId: string | null): readonly ['search-index-status', string | null] {
  return ['search-index-status', projectId]
}

export function useSearchIndexStatus(projectId: string | null, options: { queuedAt?: number | null } = {}) {
  const queuedAt = options.queuedAt ?? null
  // The function form: SWR asks it after every answer, so the poll stops on
  // the answer that shows the re-index finished. Stable per queue time, so a
  // re-render never restarts the timer.
  const refreshInterval = useCallback(
    (latest?: IndexStatus) => (reindexProgress(latest, queuedAt, Date.now()) === 'pending' ? REINDEX_POLL_MS : 0),
    [queuedAt],
  )
  const { data, isLoading, mutate } = useSWR<IndexStatus>(
    searchIndexStatusKey(projectId),
    async () => {
      try {
        return await searchService.getIndexStatus(projectId ?? undefined)
      } catch {
        return UNKNOWN_INDEX_STATUS
      }
    },
    { revalidateOnFocus: false, refreshInterval },
  )
  return { indexStatus: data ?? null, isLoading, mutate }
}
