import useSWR from 'swr'
import {
  type PolicySummary,
  getPolicy,
  listPolicies,
} from '@/services/policyService'

/**
 * Policy-editor fetches as SWR hooks.
 *
 * Replaces the two load-on-mount effects in PolicyEditorPage — the kind the
 * react-hooks `set-state-in-effect` rule (correctly) discourages, since each
 * effect drove `policies`/`loading`/`error` (and, in edit mode, the editable
 * `name`/`description`/`projectId`/`doc`/`isDraft` form fields) synchronously
 * off the route params. SWR now owns the loading/data/error state declaratively
 * and re-keys on the policy id, so the page no longer threads a `loadPolicies`
 * callback through an effect dependency array.
 *
 * `usePolicies` is gated by `enabled` so the list query only runs in list mode;
 * it returns `mutate` so the page can refresh the list after a deactivate
 * instead of calling the loader imperatively. `usePolicy` is gated by `enabled`
 * plus a concrete id, mirroring the old `if (!isNew && policyId)` branch; the
 * page seeds its editable form fields from the returned policy during render
 * (the "adjust state during render" pattern) rather than from an effect.
 *
 * `shouldRetryOnError: false` surfaces a failed load immediately, like the old
 * single-shot `try/catch`.
 */
const OPTS = { revalidateOnFocus: false, shouldRetryOnError: false } as const

const POLICIES_KEY = 'release-gate-policies'
const POLICY_KEY = 'release-gate-policy'

export function usePolicies(enabled: boolean) {
  const { data, error, isLoading, mutate } = useSWR<PolicySummary[]>(
    enabled ? ([POLICIES_KEY] as const) : null,
    () => listPolicies(),
    OPTS,
  )
  return { policies: data ?? [], isLoading, isError: !!error, mutate }
}

export function usePolicy(policyId: string | undefined, enabled: boolean) {
  const { data, error, isLoading } = useSWR<PolicySummary>(
    enabled && policyId ? ([POLICY_KEY, policyId] as const) : null,
    ([, id]: readonly [string, string]) => getPolicy(id),
    OPTS,
  )
  return { policy: data ?? null, isLoading, isError: !!error }
}
