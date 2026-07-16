import useSWR from 'swr'
import {
  type CodeownersCoverage,
  type OwnershipRule,
  getCodeownersCoverage,
  listOwnershipRules,
} from '@/services/ownershipService'

/**
 * Ownership-rules fetch as an SWR hook.
 *
 * Replaces a load-on-mount `useEffect(() => { loadRules() }, [loadRules])` in
 * OwnershipEditorPage — the kind of effect the react-hooks `set-state-in-effect`
 * rule (correctly) discourages, since the effect drove `rules`/`loading`/`error`
 * state synchronously. SWR now owns that state declaratively.
 *
 * Keyed on `projectId` exactly like the old effect's dependency array, so it
 * refetches when the active project changes. The key is `null` (no fetch) when
 * there is no resolved project — matching the old `if (!projectId) return`
 * guard, where the all-projects view (`null` projectId) showed the "select a
 * project" empty state and never fetched. `shouldRetryOnError: false` surfaces
 * a failed load immediately, mirroring the old single-shot `.catch`.
 */
export function useOwnershipRules(projectId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<OwnershipRule[]>(
    projectId ? (['ownership-rules', projectId] as const) : null,
    ([, pid]: readonly [string, string]) => listOwnershipRules(pid),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  return {
    rules: data ?? [],
    isLoading,
    isError: !!error,
    refresh: mutate,
  }
}

/**
 * CODEOWNERS coverage badge data (US-8.3). Keyed on `projectId`; no fetch when
 * there is no resolved project (mirrors `useOwnershipRules`). `refresh` lets
 * the page re-pull after an import updates the rule set.
 */
export function useCodeownersCoverage(projectId: string | null) {
  const { data, error, isLoading, mutate } = useSWR<CodeownersCoverage>(
    projectId ? (['codeowners-coverage', projectId] as const) : null,
    ([, pid]: readonly [string, string]) => getCodeownersCoverage(pid),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )

  return {
    coverage: data ?? null,
    isLoading,
    isError: !!error,
    refresh: mutate,
  }
}
