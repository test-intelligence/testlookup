import useSWR, { mutate as globalMutate } from 'swr'
import { featureFlagService, type FeatureFlag } from '@/services/featureFlagService'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'

/**
 * Revalidate EVERY key that answers "what is the state of a feature flag".
 *
 * There are two: `'feature-flags'` (the admin table) and
 * `['feature-flag-status', key, projectId]` (the resolved per-project gate the
 * rest of the app actually branches on). The admin page used to refresh only
 * the first, so toggling a flag updated the table and changed nothing else.
 *
 * That was not self-healing. `useFeatureEnabled` sets `revalidateOnFocus:false`
 * and no `refreshInterval`, and its most important consumer — `Sidebar` — is
 * rendered outside `<Outlet/>`, so it never unmounts and SWR's revalidate-on-
 * mount repair never fires. Disabling `manual_upload` left the Upload nav entry
 * present for the rest of the session.
 *
 * One helper so both key spaces go through the same place.
 */
export function refreshFeatureFlags() {
  return globalMutate(
    (key) =>
      key === 'feature-flags' ||
      (Array.isArray(key) && key[0] === 'feature-flag-status'),
    undefined,
    { revalidate: true },
  )
}

export function useFeatureFlags() {
  const { data, error, isLoading } = useSWR<FeatureFlag[]>(
    'feature-flags',
    () => featureFlagService.list(),
    { revalidateOnFocus: false },
  )
  return {
    flags: data ?? [],
    isLoading,
    isError: !!error,
    refresh: refreshFeatureFlags,
  }
}

/**
 * Resolved enabled-state of a single flag for the active project — respects the
 * global toggle + project/role allow-lists + rollout percent (server-side). For
 * gating UI behind a rollout flag (e.g. `manual_upload`). Defaults false until
 * loaded. SWR dedupes identical (key, project) lookups across components.
 */
export function useFeatureEnabled(key: string): boolean {
  const activeProjectId = useProjectStore((s) => s.activeProjectId)
  const projectId = activeProjectId === ALL_PROJECTS_ID ? null : activeProjectId
  const { data } = useSWR(
    ['feature-flag-status', key, projectId],
    () => featureFlagService.status(key, projectId),
    { revalidateOnFocus: false },
  )
  return data?.enabled ?? false
}
