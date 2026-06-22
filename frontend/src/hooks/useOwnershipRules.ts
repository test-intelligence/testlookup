import useSWR from 'swr'
import { type OwnershipRule, listOwnershipRules } from '@/services/ownershipService'

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
