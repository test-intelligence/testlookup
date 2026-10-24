import { useEffect, useState } from 'react'
import useSWR from 'swr'
import { searchService } from '@/services/searchService'
import type { GlobalSearchResponse } from '@/types/search'

/**
 * W12 SRCH-03 — the top-bar typeahead's read: ONE `GET /search/global?q=&size=15`
 * 250 ms after the last keystroke, with at least 2 characters, while the list
 * is open (`enabled`). Never recorded (no `record`), no global toast.
 *
 * 15 candidates for 5 rows: one test that ran in many runs comes back once per
 * run, and the list shows DISTINCT entities (`distinctMatches`, the first row
 * per entity type + title in the server's order).
 *
 * The SWR key is the SETTLED query — the trimmed text once it has stayed the
 * same for 250 ms — and there is no key while the text is still changing, so
 * an answer for an older query is never rendered: SWR only hands back the
 * current key's data. Closing the list (Escape, a blur, a navigation) drops
 * the key: no request.
 */
export const TYPEAHEAD_DEBOUNCE_MS = 250
export const TYPEAHEAD_MIN_CHARS = 2
/** Candidates asked for, so five DISTINCT entities (searchModel's TYPEAHEAD_ROWS) can remain after the dedupe. */
export const TYPEAHEAD_FETCH_SIZE = 15

export function useSearchTypeahead(query: string, projectId: string | null, enabled: boolean) {
  const trimmed = query.trim()
  const eligible = enabled && trimmed.length >= TYPEAHEAD_MIN_CHARS
  const [settled, setSettled] = useState<string | null>(null)

  useEffect(() => {
    if (!eligible) return undefined
    const timer = setTimeout(() => setSettled(trimmed), TYPEAHEAD_DEBOUNCE_MS)
    return () => clearTimeout(timer)
  }, [eligible, trimmed])

  const debounced = eligible && settled === trimmed ? trimmed : null
  const key = debounced ? (['search-typeahead', projectId, debounced] as const) : null
  const swr = useSWR<GlobalSearchResponse>(
    key,
    // The list says "Could not load matches." itself: no global toast per settled query.
    ([, project, q]: readonly [string, string | null, string]) =>
      searchService.globalSearch({ q, ...(project ? { project_id: project } : {}), size: TYPEAHEAD_FETCH_SIZE }, { suppressToast: true }),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
  return {
    /** The query the shown answer belongs to; null while typing, under 2 characters or closed. */
    debounced,
    /** Waiting for the text to settle (typed within the last 250 ms). */
    settling: eligible && !debounced,
    data: debounced ? swr.data : undefined,
    error: debounced ? swr.error : undefined,
    isLoading: debounced ? swr.isLoading : false,
  }
}
