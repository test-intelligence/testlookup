import useSWR from 'swr'
import { searchService } from '@/services/searchService'
import type { SearchSuggestions } from '@/types/search'

/**
 * W12 SRCH-03 — suggested searches for ONE project (failing tests of the last
 * 7 days, open defects, the active release). Asked only with a project pinned
 * and the search box empty (`enabled`); All Projects asks nothing.
 */
export function useSuggestedSearches(projectId: string | null, enabled: boolean) {
  const key = enabled && projectId ? (['search-suggested', projectId] as const) : null
  return useSWR<SearchSuggestions>(key, ([, id]: readonly [string, string]) => searchService.suggested(id), {
    revalidateOnFocus: false,
  })
}
