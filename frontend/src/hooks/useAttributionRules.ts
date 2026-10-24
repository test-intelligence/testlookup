import useSWR from 'swr'
import { attributionRulesService } from '@/services/attributionRulesService'
import type { AttributionRuleFieldsResponse, AttributionRuleList } from '@/types/attributionRules'

/**
 * The project's attribution rules, in the order the ladder tries them (W11 REL-09).
 *
 * Keyed on the project the PAGE pinned: `null` (All Projects) asks nothing,
 * since the page renders the project prompt instead. No poll: rules change
 * only through this page, which revalidates after each write.
 */
export function useAttributionRules(projectId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<AttributionRuleList>(
    projectId ? (['attribution-rules', projectId] as const) : null,
    ([, pid]: readonly [string, string]) => attributionRulesService.list(pid),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
  return { data, error, isLoading, mutate }
}

/** The fields a rule can match on (`GET …/fields`): static per deployment, read once per project. */
export function useAttributionRuleFields(projectId: string | null) {
  const { data, error } = useSWR<AttributionRuleFieldsResponse>(
    projectId ? (['attribution-rule-fields', projectId] as const) : null,
    ([, pid]: readonly [string, string]) => attributionRulesService.fields(pid),
    { revalidateOnFocus: false, revalidateIfStale: false, shouldRetryOnError: false },
  )
  return { data, error }
}
