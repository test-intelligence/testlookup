import { useCallback, useEffect, useRef } from 'react'
import useSWR, { useSWRConfig } from 'swr'

import { appSettingsService, type EmbeddingIndex } from '@/services/appSettingsService'

/**
 * The embedding index on Settings › AI (W12 SRCH-02, contract §1.4 / §4.2).
 *
 * `GET /settings/ai` already carries `embedding_index`, so the page passes it in as `initial` and this hook
 * asks NOTHING on load (`revalidateOnMount: false`): `GET /settings/ai/embedding-index` is read only while a
 * re-embed is `building`, every 5 s, and stops at the first answer that is not.
 *
 * The interval is a NUMBER derived from what is known now (the cached answer, else `initial`), not SWR's
 * function form: SWR calls that function with the CACHED data only, which is empty while the page's own
 * `initial` is all there is, so a page that loads mid-build would never poll (the `useMLStatus` precedent).
 * A new answer re-renders this hook, the number changes, and SWR re-arms or stops its timer at once.
 */
export const EMBEDDING_INDEX_KEY = 'settings/ai-embedding-index'
export const EMBEDDING_INDEX_POLL_MS = 5000

/** 5 s while a LIVE build runs; nothing while idle, failed or stalled (no worker is moving it: Re-embed now resumes). */
export function embeddingIndexRefreshInterval(index: EmbeddingIndex | null | undefined): number {
  return index?.status === 'building' && index.stalled !== true ? EMBEDDING_INDEX_POLL_MS : 0
}

export function useEmbeddingIndex(initial: EmbeddingIndex | null | undefined) {
  const { cache } = useSWRConfig()
  const cached = cache.get(EMBEDDING_INDEX_KEY)?.data as EmbeddingIndex | undefined
  const swr = useSWR<EmbeddingIndex>(
    EMBEDDING_INDEX_KEY,
    () => appSettingsService.getEmbeddingIndex(),
    {
      fallbackData: initial ?? undefined,
      revalidateOnMount: false,
      revalidateOnFocus: false,
      revalidateOnReconnect: false,
      refreshInterval: embeddingIndexRefreshInterval(cached ?? initial),
    },
  )
  const { mutate } = swr
  /** Show an answer the page already holds (a Save's or a Re-embed now's response) without asking again. */
  const show = useCallback(
    (next: EmbeddingIndex | null | undefined) => mutate(next ?? undefined, { revalidate: false }),
    [mutate],
  )
  // A FRESH `initial` (the page's GET /settings/ai on load, or a Save's answer) wins over what the cache kept
  // from an earlier visit: an idle answer cached before the reader left would otherwise hide a build started
  // since (and never poll it). Compared by content, so a caller that rebuilds the same object each render
  // does not overwrite a newer poll.
  const seeded = useRef<string | undefined>(undefined)
  useEffect(() => {
    if (initial === undefined) return
    const content = JSON.stringify(initial)
    if (seeded.current === content) return
    seeded.current = content
    void mutate(initial ?? undefined, { revalidate: false })
  }, [initial, mutate])
  return {
    /** The latest answer: the poll's, else what `GET /settings/ai` (or a write) returned; null = unreadable. */
    index: (swr.data ?? initial ?? null) as EmbeddingIndex | null,
    /** A failed poll (the last known state stays shown). */
    error: swr.error as unknown,
    show,
  }
}
