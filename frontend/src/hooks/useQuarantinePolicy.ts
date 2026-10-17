/**
 * W09 FLK-02: one project's quarantine lifecycle policy, for the Policy tab's
 * card and the Quarantined tab's over-cap warning on Flaky tests (`/flaky`).
 *
 * Its own module, NOT `useFlakyQuarantine.ts`: that module is fixed-factory
 * mocked in three test files, where a hook added to it would be `undefined`.
 *
 * Asks nothing unless `enabled` and a project is given: the policy is read on
 * the Policy and Quarantined tabs only (never Detected, Proposed or History,
 * whose request inventories are pinned). Both tabs share ONE cache entry, so
 * a save on the Policy tab is what the Quarantined tab warns with. The answer
 * is parsed here (`parseQuarantinePolicy`): a malformed one is `policy: null`.
 */
import useSWR from 'swr'
import {
  parseQuarantinePolicy,
  quarantinePolicyService,
  type QuarantinePolicy,
} from '@/services/quarantinePolicyService'

export const quarantinePolicyKey = (projectId: string) => ['quarantine-policy', projectId] as const

export function useQuarantinePolicy(
  projectId: string | null | undefined,
  enabled: boolean,
): {
  policy: QuarantinePolicy | null
  error: unknown
  isLoading: boolean
  /** Revalidate, or (with a value) store that answer without asking again. */
  mutate: (next?: QuarantinePolicy) => Promise<unknown>
} {
  const { data, error, isLoading, mutate } = useSWR<unknown>(
    enabled && projectId ? quarantinePolicyKey(projectId) : null,
    () => quarantinePolicyService.get(projectId as string),
    // A failed read is shown once, with Retry; no background retry loop.
    { revalidateOnFocus: false, shouldRetryOnError: false },
  )
  return {
    policy: parseQuarantinePolicy(data),
    error,
    isLoading,
    mutate: (next?: QuarantinePolicy) => (next ? mutate(next, { revalidate: false }) : mutate()),
  }
}
