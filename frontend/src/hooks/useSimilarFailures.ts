import useSWR from 'swr'
import { searchService } from '@/services/searchService'
import { SIMILAR_LIMIT } from '@/components/search/similarFailuresModel'
import type { SimilarFailuresResponse } from '@/types/search'

/**
 * W12 SRCH-01 — the failed result's nearest failed / broken neighbours in its
 * project's semantic index. Asked only while the Similar failures tab is open
 * (`enabled`): a test page's load sends nothing new.
 */
export function useSimilarFailures(testId: string, enabled: boolean) {
  const key = enabled && testId ? (['search-similar', testId] as const) : null
  return useSWR<SimilarFailuresResponse>(
    key,
    ([, id]: readonly [string, string]) => searchService.findSimilar(id, SIMILAR_LIMIT),
    // A refusal (403 for another project's result) is said at once, not retried.
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
}
