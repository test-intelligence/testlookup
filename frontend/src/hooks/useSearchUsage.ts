import useSWR from 'swr'
import { searchService } from '@/services/searchService'
import type { SearchUsage } from '@/types/search'

/**
 * W12 SRCH-05 — today's search usage (UTC day) for the pinned project, or for
 * every project the reader can see. QA lead and admin only (`enabled`): the
 * route refuses the other roles, so they send nothing.
 */
export const USAGE_DAYS_TODAY = 1

export function useSearchUsage(projectId: string | null, enabled: boolean) {
  const key = enabled ? (['search-usage', projectId, USAGE_DAYS_TODAY] as const) : null
  return useSWR<SearchUsage>(key, () => searchService.usage(projectId, USAGE_DAYS_TODAY), { revalidateOnFocus: false })
}
