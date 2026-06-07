import useSWR from 'swr'
import { featureFlagService, type FeatureFlag } from '@/services/featureFlagService'
import { ALL_PROJECTS_ID, useProjectStore } from '@/store/projectStore'

export function useFeatureFlags() {
  const { data, error, isLoading, mutate } = useSWR<FeatureFlag[]>(
    'feature-flags',
    () => featureFlagService.list(),
    { revalidateOnFocus: false },
  )
  return { flags: data ?? [], isLoading, isError: !!error, refresh: mutate }
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
